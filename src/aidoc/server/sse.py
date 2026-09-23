"""Event bus + `/api/events` (spec §6, §8.1): every event is written to SQLite first, then fanned out.

Reconnects replay from the `events` table after `Last-Event-ID`; a gap older than the retained events (or a
subscriber that fell too far behind) gets `event: resync` (no `id:` line) so the client refetches state."""
from __future__ import annotations

import asyncio
import json
import queue
import threading

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

PING_INTERVAL_S = 15.0
_POLL_S = 1.0                     # how often the stream checks for client disconnect while idle


def format_event(seq: int, kind: str, data: dict) -> str:
    return f"id: {seq}\nevent: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


RESYNC_FRAME = "event: resync\ndata: {}\n\n"
PING_FRAME = ": ping\n\n"


class Subscription:
    def __init__(self, bus: EventBus, maxsize: int):
        self.bus = bus
        self.queue: queue.Queue[tuple[int, str, dict]] = queue.Queue(maxsize=maxsize)
        self.overflowed = False

    def put(self, item: tuple[int, str, dict]) -> None:
        try:
            self.queue.put_nowait(item)
        except queue.Full:
            self.overflowed = True

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
    ("resync", []) when events the client has not seen are gone."""
    if last_event_id is None:
        return "live", []
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


router = APIRouter()


@router.get("/events")
async def events(request: Request):
    ctx = request.app.state.ctx
    bus: EventBus = ctx.bus
    last_id = _parse_last_id(request)
    sub = bus.subscribe()                       # before reading the DB, so nothing falls between replay and live
    mode, backlog = replay_plan(ctx.store, last_id)

    async def gen():
        try:
            sent = last_id if last_id is not None and last_id >= 0 else 0
            if mode == "resync":
                yield RESYNC_FRAME
            for e in backlog:
                yield format_event(e["seq"], e["kind"], e["payload"])
                sent = e["seq"]
            if mode == "live":
                sent = 0
            idle = 0.0
            while True:
                if await request.is_disconnected():
                    return
                if sub.overflowed:              # we fell behind: drop what is queued, ask for a refetch
                    while not sub.queue.empty():
                        sub.queue.get_nowait()
                    sub.overflowed = False
                    yield RESYNC_FRAME
                try:
                    seq, kind, payload = await asyncio.to_thread(sub.queue.get, True, _POLL_S)
                except queue.Empty:
                    idle += _POLL_S
                    if idle >= PING_INTERVAL_S:
                        idle = 0.0
                        yield PING_FRAME
                    continue
                idle = 0.0
                if seq <= sent:                 # already replayed from the DB
                    continue
                sent = seq
                yield format_event(seq, kind, payload)
        finally:
            sub.close()

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
