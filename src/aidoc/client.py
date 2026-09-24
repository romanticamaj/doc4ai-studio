"""CLI -> server forwarding (spec §8.1): when `aidoc serve` owns the queue, the CLI submits jobs to it and follows
them over `/api/events` instead of converting in-process."""
from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx

from aidoc import lockfile
from aidoc.models import ConvertOptions

TERMINAL_JOB = {"done", "cancelled"}


class ServerError(Exception):
    def __init__(self, status: int, body: dict | str):
        self.status, self.body = status, body
        err = body.get("error") if isinstance(body, dict) else body
        super().__init__(f"server replied {status}: {err}")


class ServerGone(Exception):
    """The server stopped while we were following a job."""


class ServerClient:
    lock_path: Path | None = None            # set by find_server: lets follow_job notice the server has exited

    def __init__(self, base_url: str, token: str | None, transport: httpx.BaseTransport | None = None,
                 timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.token = token
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._http = httpx.Client(base_url=self.base_url, headers=headers, transport=transport,
                                  timeout=httpx.Timeout(timeout, read=timeout))

    def close(self) -> None:
        self._http.close()

    def _json(self, r: httpx.Response) -> dict:
        try:
            body = r.json()
        except ValueError:
            body = r.text
        if r.status_code >= 400:
            raise ServerError(r.status_code, body)
        return body

    # ------------------------------------------------------------------ calls
    def create_job(self, paths: list[Path], opts: ConvertOptions) -> dict:
        body = {"inputs": [{"path": str(Path(p).resolve())} for p in paths], "engine": opts.engine,
                "lang": opts.lang, "force": opts.force, "retry_low": opts.retry_low,
                "allow_online_audio": opts.allow_online_audio, "origin": "cli",
                "output_dir": str(Path(opts.output_dir).resolve()), "timeout_s": opts.timeout_s}
        # the server hashes every input before answering: no read timeout for big batches
        return self._json(self._http.post("/api/jobs", json=body, timeout=httpx.Timeout(30.0, read=None)))["job"]

    def get_job(self, job_id: str) -> dict:
        return self._json(self._http.get(f"/api/jobs/{job_id}"))

    def cancel_job(self, job_id: str) -> dict:
        return self._json(self._http.post(f"/api/jobs/{job_id}/cancel"))["job"]

    # ------------------------------------------------------------------ SSE
    def _frames(self, last_id: int | None, on_open: Callable[[], None]) -> Iterator[tuple[int | None, str, dict]]:
        headers = {"Last-Event-ID": str(last_id)} if last_id is not None else {}
        with self._http.stream("GET", "/api/events", headers=headers,
                               timeout=httpx.Timeout(30.0, read=40.0)) as r:   # the server pings every 15 s
            if r.status_code >= 400:
                raise ServerError(r.status_code, r.read().decode("utf-8", "replace"))
            on_open()
            yield None, "_open", {}                # lets the caller act on the state fetched in on_open
            seq, kind, data = None, "message", []
            for line in r.iter_lines():
                if line == "":
                    if data:
                        try:
                            payload = json.loads("\n".join(data))
                        except ValueError:
                            payload = {}
                        yield seq, kind, payload
                    seq, kind, data = None, "message", []
                    continue
                if line.startswith(":"):
                    yield None, "ping", {}
                    continue
                field, _, value = line.partition(":")
                value = value.removeprefix(" ")
                if field == "id":
                    seq = int(value) if value.isdigit() else None
                elif field == "event":
                    kind = value
                elif field == "data":
                    data.append(value)

    def follow_job(self, job_id: str, on_event: Callable[[str, dict], None], stop: threading.Event) -> str:
        """Stream events until the job is terminal; returns its final status ("done" / "cancelled"), or
        "detached" when `stop` is set. Reconnects with Last-Event-ID; `resync` refetches the job."""
        last_id: int | None = None
        state: dict = {}
        failures = 0

        def check_job() -> None:
            d = self.get_job(job_id)
            state["status"] = d["job"]["status"]
            state["tasks"] = d.get("tasks") or []

        while not stop.is_set():
            try:
                for seq, kind, payload in self._frames(last_id, check_job):
                    if state.get("status") in TERMINAL_JOB:
                        return state["status"]
                    if stop.is_set():
                        return "detached"
                    if seq is not None:
                        last_id = seq
                    if kind == "_open" and last_id is None:
                        # live mode starts now: report the state tasks reached before the stream opened
                        # (P3 verifier: a forwarded batch lost the first status line of an early task)
                        for t in state.pop("tasks", []):
                            on_event("task.updated", {k: v for k, v in t.items() if k != "segments"})
                        continue
                    if kind == "resync":
                        last_id = None
                        check_job()
                        continue
                    if kind in ("task.updated", "segment.updated", "task.log"):
                        jid = payload.get("job_id")
                        if kind == "task.updated" and jid != job_id:
                            continue
                        on_event(kind, payload)
                    elif kind == "job.updated" and payload.get("id") == job_id:
                        on_event(kind, payload)
                        if payload.get("status") in TERMINAL_JOB:
                            return payload["status"]
                if state.get("status") in TERMINAL_JOB:
                    return state["status"]
                failures = 0
            except (httpx.TransportError, httpx.HTTPError):
                failures += 1                      # network blip: reconnect with Last-Event-ID, unless it is gone
                if self.lock_path is not None and not lockfile.is_live(lockfile.read_lock(self.lock_path)):
                    raise ServerGone(self.base_url) from None
                if failures >= 30:
                    raise ServerGone(self.base_url) from None
                time.sleep(1.0)
        return "detached"


def cli_token(config, explicit: str | None = None) -> str | None:
    """The API token the CLI presents: --token, else AIDOC_TOKEN, else aidoc.toml server.token (never the lock)."""
    return explicit or os.environ.get("AIDOC_TOKEN") or config.server.token or None


def find_server(config, token: str | None = None) -> ServerClient | None:
    """A client for the live server that owns data/aidoc.lock, or None."""
    info = lockfile.read_lock(Path(config.data_dir) / "aidoc.lock")
    if not lockfile.is_live(info) or not info.get("port"):
        return None
    host = info.get("host") or "127.0.0.1"
    if host in ("0.0.0.0", "::", ""):
        host = "127.0.0.1"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    client = ServerClient(f"http://{host}:{info['port']}", cli_token(config, token))
    client.lock_path = Path(config.data_dir) / "aidoc.lock"
    return client
