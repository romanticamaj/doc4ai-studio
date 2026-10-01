"""One `mcp_calls` row per MCP HTTP request, observed clients and `last_used_*` (MCP spec §3, §4.3 step 4, §7 SSE).
Never stores or publishes a raw token or full arguments."""
from __future__ import annotations

import hashlib
import json
import threading
import time
import traceback
from typing import Any

ACTIVE_WINDOW_S = 300          # spec §3: a client is active when last_seen is within 5 minutes
TOUCH_THROTTLE_S = 10          # spec §4.3 step 4
ARGS_MAX = 500
IDLE_SWEEP_S = 30
IDENTITY_TTL_S = 3600          # spike S5: legacy stateless clients identify themselves on `initialize` only
_REDACT_KEYS = ("content_base64",)


def summarize_args(args: dict | None) -> str | None:
    if args is None:
        return None
    clean: dict[str, Any] = {}
    for k, v in args.items():
        if k in _REDACT_KEYS and isinstance(v, str):
            clean[k] = {"len": len(v), "sha256_8": hashlib.sha256(v.encode("utf-8")).hexdigest()[:8]}
        else:
            clean[k] = v
    s = json.dumps(clean, ensure_ascii=False, default=str)
    return s if len(s) <= ARGS_MAX else s[: ARGS_MAX - 1] + "…"


class CallRecorder:
    def __init__(self, ctx):
        self.ctx = ctx
        self._touched: dict[str, float] = {}
        self._identities: dict[tuple, tuple[str, str | None, float]] = {}   # (token_id, ua) -> (name, version, ts)
        self._active: dict[str, float] = {}             # client_id -> last_seen we announced as active
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.sweepers: list = []                        # extra periodic jobs (auth-flood flush), run by the watcher

    # ---- rows
    def record(self, *, status: str, token_id=None, token_prefix_seen=None, client_id=None, method=None, tool_name=None,
               resource_uri=None, args=None, error_code=None, http_status=None, duration_ms=None, response_bytes=None,
               response_tokens_est=None, ip=None, protocol_version=None, job_id=None, ts=None) -> int:
        store = self.ctx.store
        ts = time.time() if ts is None else ts
        rid = store.insert_mcp_call(
            status=status, ts=ts, token_id=token_id, token_prefix_seen=token_prefix_seen, client_id=client_id,
            method=method, tool_name=tool_name, resource_uri=(resource_uri or None) and resource_uri[:200],
            args_summary=summarize_args(args), error_code=error_code, http_status=http_status, duration_ms=duration_ms,
            response_bytes=response_bytes, response_tokens_est=response_tokens_est, ip=ip,
            protocol_version=protocol_version, job_id=job_id)
        self._publish_call(store.get_mcp_call(rid))
        return rid

    def update_call(self, call_id: int, *, args=None) -> None:
        """Rewrite an aggregate row's summary (auth flood counts) and re-send it as the same live event id."""
        self.ctx.store.update_mcp_call(call_id, args_summary=summarize_args(args))
        row = self.ctx.store.get_mcp_call(call_id)
        if row is not None:
            self._publish_call(row)

    def _publish_call(self, row: dict) -> None:
        store = self.ctx.store
        token = store.get_api_token(row["token_id"]) if row["token_id"] else None
        client = store.get_mcp_client(row["client_id"]) if row["client_id"] else None
        self.ctx.bus.publish("mcp.call", str(row["id"]), {
            # display fields only: the 15-char token prefix seen on failures, never args_summary or the token
            **{k: row[k] for k in ("id", "ts", "token_id", "client_id", "method", "tool_name", "status", "error_code",
                                   "http_status", "duration_ms", "response_tokens_est", "job_id", "token_prefix_seen",
                                   "ip", "protocol_version", "response_bytes", "resource_uri")},
            "token_name": token["name"] if token else None, "client_name": client["client_name"] if client else None})

    # ---- clients
    def _client_payload(self, row: dict, state: str) -> dict:
        token = self.ctx.store.get_api_token(row["token_id"]) if row.get("token_id") else None
        return {**{k: row[k] for k in ("id", "token_id", "client_name", "client_version", "protocol_version", "user_agent",
                                      "first_seen", "last_seen", "last_ip", "request_count")},
                "token_name": token["name"] if token else None, "state": state,
                "active": state != "idle"}

    def note_client(self, *, token_id, client_name, client_version, protocol_version, user_agent, ip, now=None) -> str:
        now = time.time() if now is None else now
        cid, created = self.ctx.store.upsert_mcp_client(token_id=token_id, client_name=client_name or "unknown",
                                                        client_version=client_version, protocol_version=protocol_version,
                                                        user_agent=user_agent, ip=ip, now=now)
        with self._lock:
            was_active = cid in self._active
            self._active[cid] = now
        if created:
            self.ctx.bus.publish("mcp.client", cid, self._client_payload(self.ctx.store.get_mcp_client(cid), "new"))
        elif not was_active:
            self.ctx.bus.publish("mcp.client", cid, self._client_payload(self.ctx.store.get_mcp_client(cid), "active"))
        return cid

    def sweep_idle(self, now=None) -> list[str]:
        now = time.time() if now is None else now
        with self._lock:
            idle = [cid for cid, seen in self._active.items() if now - seen > ACTIVE_WINDOW_S]
            for cid in idle:
                del self._active[cid]
        for cid in idle:
            row = self.ctx.store.get_mcp_client(cid)
            if row is not None:
                self.ctx.bus.publish("mcp.client", cid, self._client_payload(row, "idle"))
        return idle

    def remember_identity(self, token_id, user_agent, name: str, version: str | None, now=None) -> None:
        now = time.time() if now is None else now
        with self._lock:
            self._identities[(token_id, user_agent)] = (name, version, now)
            if len(self._identities) > 1000:                # bounded: drop the oldest
                oldest = min(self._identities, key=lambda k: self._identities[k][2])
                del self._identities[oldest]

    def recall_identity(self, token_id, user_agent, now=None) -> tuple[str | None, str | None]:
        now = time.time() if now is None else now
        with self._lock:
            hit = self._identities.get((token_id, user_agent))
        if hit is None or now - hit[2] > IDENTITY_TTL_S:
            return None, None
        return hit[0], hit[1]

    # ---- tokens
    def touch_token(self, token_id: str, ip: str | None, client_label: str | None, now=None) -> bool:
        now = time.time() if now is None else now
        with self._lock:
            last = self._touched.get(token_id)
            if last is not None and now - last < TOUCH_THROTTLE_S:
                return False
            self._touched[token_id] = now
        self.ctx.store.update_api_token(token_id, last_used_at=now, last_used_ip=ip, last_client=client_label)
        return True

    # ---- watcher
    def _loop(self) -> None:
        while not self._stop.wait(IDLE_SWEEP_S):
            try:
                self.sweep_idle()
                for fn in self.sweepers:
                    fn()
            except Exception:  # noqa: BLE001  a watcher must never take the server down
                traceback.print_exc()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="aidoc-mcp-idle", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(5)
