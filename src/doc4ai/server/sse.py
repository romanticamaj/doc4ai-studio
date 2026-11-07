"""Event bus + `/api/events` (spec §6, §8.1): every event is written to SQLite first, then fanned out.

Reconnects replay from the `events` table after `Last-Event-ID`; a gap older than the retained events, an id
from the future (the DB was reset), or a subscriber that fell too far behind gets `event: resync` (no `id:` line)
so the client refetches state.

Streams are asyncio-native (P3 verifier #5): the bus appends to a per-subscriber deque and wakes the stream's
event loop with `call_soon_threadsafe`; no stream parks a worker thread, so many open streams cannot starve the
thread pool that serves uploads and other sync endpoints."""
from __future__ import annotations

import asyncio
import collections
import json
import threading
from collections.abc import AsyncIterator, Awaitable, Callable

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

PING_INTERVAL_S = 15.0
_DISCONNECT_CHECK_S = 1.0          # how often an idle stream checks for client disconnect
WORKSPACE = "default"


def format_event(seq: int, kind: str, data: dict) -> str:
    if isinstance(data, dict) and "workspace" not in data:
        data = {**data, "workspace": WORKSPACE}
    return f"id: {seq}\nevent: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


RESYNC_FRAME = "event: resync\ndata: {}\n\n"
PING_FRAME = ": ping\n\n"


class Subscription:
    def __init__(self, bus: EventBus, maxsize: int):
        self.bus = bus
        self.maxsize = maxsize
        self.items: collections.deque[tuple[int, str, dict]] = collections.deque()
        self.overflowed = False
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._event: asyncio.Event | None = None

    def bind(self, loop: asyncio.AbstractEventLoop) -> asyncio.Event:
        """Attach to the stream's event loop; returns the event that `put` sets."""
        self._loop = loop
        self._event = asyncio.Event()
        if self.items or self.overflowed:
            self._event.set()
        return self._event

    def put(self, item: tuple[int, str, dict]) -> None:
        with self._lock:
            if len(self.items) >= self.maxsize:
                self.overflowed = True
            else:
                self.items.append(item)
        loop, ev = self._loop, self._event
        if loop is not None and ev is not None:
            try:
                loop.call_soon_threadsafe(ev.set)
            except RuntimeError:            # loop closed: the stream is gone
                pass

    def drain(self) -> tuple[bool, list[tuple[int, str, dict]]]:
        """(overflowed, queued items); an overflow drops what is queued."""
        with self._lock:
            if self.overflowed:
                self.items.clear()
                self.overflowed = False
                return True, []
            items = list(self.items)
            self.items.clear()
            return False, items

    def close(self) -> None:
        self.bus._remove(self)


class EventBus:
    def __init__(self, store):
        self.store = store
        self._lock = threading.Lock()
        self._subs: list[Subscription] = []

    def publish(self, kind: str, ref_id: str | None, payload: dict) -> int:
        """DB first, then fan-out; the lock keeps fan-out order equal to seq order across threads."""
        with self._lock:
            seq = self.store.append_event(kind, ref_id, payload)
            for s in list(self._subs):
                s.put((seq, kind, payload))
        return seq

    def subscribe(self, maxsize: int = 10000) -> Subscription:
        sub = Subscription(self, maxsize)
        with self._lock:
            self._subs.append(sub)
        return sub

    def _remove(self, sub: Subscription) -> None:
        with self._lock:
            if sub in self._subs:
                self._subs.remove(sub)

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subs)


def replay_plan(store, last_event_id: int | None) -> tuple[str, list[dict]]:
    """("live", []) without an id; ("replay", events after it) when nothing in between was pruned;
    ("resync", []) when events the client has not seen are gone, or its id is newer than any event here
    (the DB was replaced: the client's state belongs to another history)."""
    if last_event_id is None:
        return "live", []
    newest = store.newest_event_seq()
    if last_event_id > (newest or 0):             # includes an empty table after a DB reset (final review I2)
        return "resync", []
    oldest = store.oldest_event_seq()
    if oldest is None:
        return "replay", []
    if last_event_id >= oldest - 1:
        return "replay", store.events_since(last_event_id)
    return "resync", []


def _parse_last_id(request: Request) -> int | None:
    raw = request.headers.get("last-event-id") or request.query_params.get("last_event_id")
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except ValueError:
        return -1                               # garbage id: treat as "too old" -> resync


async def event_frames(bus: EventBus, store, sub: Subscription, last_id: int | None,
                       disconnected: Callable[[], Awaitable[bool]]) -> AsyncIterator[str]:
    """The SSE frames for one client: replay (or resync), then live events, pings while idle."""
    event = sub.bind(asyncio.get_running_loop())
    try:
        mode, backlog = replay_plan(store, last_id)
        sent = last_id if mode == "replay" and last_id is not None and last_id >= 0 else 0
        if mode == "resync":
            yield RESYNC_FRAME
        for e in backlog:
            yield format_event(e["seq"], e["kind"], e["payload"])
            sent = e["seq"]
        idle = 0.0
        while True:
            overflowed, items = sub.drain()
            if overflowed:                          # we fell behind: ask for a refetch
                yield RESYNC_FRAME
            for seq, kind, payload in items:
                if seq <= sent:                     # already replayed from the DB
                    continue
                sent = seq
                yield format_event(seq, kind, payload)
            if overflowed or items:
                idle = 0.0
                continue
            try:
                await asyncio.wait_for(event.wait(), _DISCONNECT_CHECK_S)
                event.clear()
            except asyncio.TimeoutError:
                idle += _DISCONNECT_CHECK_S
                if await disconnected():
                    return
                if idle >= PING_INTERVAL_S:
                    idle = 0.0
                    yield PING_FRAME
    finally:
        sub.close()


router = APIRouter()


@router.get("/events")
async def events(request: Request):
    ctx = request.app.state.ctx
    last_id = _parse_last_id(request)
    sub = ctx.bus.subscribe()                   # before reading the DB, so nothing falls between replay and live
    return StreamingResponse(event_frames(ctx.bus, ctx.store, sub, last_id, request.is_disconnected),
                             media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
