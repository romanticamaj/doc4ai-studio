"""Failed-auth flood control (S-phase finding 2): per-IP aggregation + 429 cool-down, still visible in the log."""
import json
import time

from starlette.testclient import TestClient

from aidoc.mcp.authguard import AuthFailureGuard
from aidoc.mcp.calllog import CallRecorder
from aidoc.mcp.gate import McpGate
from aidoc.mcp.principal import PatVerifier
from aidoc.mcp.ratelimit import RateLimiter
from tests.mcp.test_gate import MODERN, SECRET, _issue, echo_inner

LIST = {**MODERN, "Mcp-Method": "tools/list"}


def _rows(ctx):
    return ctx.store.list_mcp_calls(limit=500)


def _events(ctx):
    return [e["payload"] for e in ctx.store.events_since(0) if e["kind"] == "mcp.call"]


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def test_guard_logs_the_first_failures_then_aggregates(ctx):
    clock = Clock()
    g = AuthFailureGuard(CallRecorder(ctx), limit=3, window_s=60, cooldown_s=60, flush_s=5, clock=clock)
    for _ in range(3):
        assert g.on_failure("10.0.0.1", "unknown_token", "doc4ai_pat_AAAA") is None     # logged individually
    retry = g.on_failure("10.0.0.1", "unknown_token", "doc4ai_pat_BBBB")
    assert retry == 60                                                               # blocked: 429 + Retry-After
    rows = _rows(ctx)
    assert len(rows) == 1                       # the guard writes only the aggregate row; the gate writes the 3 above
    agg = rows[0]
    assert (agg["status"], agg["error_code"], agg["http_status"], agg["ip"]) == ("rate_limited", "auth_failures", 429, "10.0.0.1")
    for i in range(50):
        clock.t += 0.01
        assert g.on_failure("10.0.0.1", "bad_checksum", None) is not None
    assert len(_rows(ctx)) == 1                                                       # no row per attempt
    clock.t += 5
    g.sweep()
    summary = json.loads(_rows(ctx)[0]["args_summary"])
    assert summary["suppressed"] == 51 and summary["reasons"] == {"unknown_token": 1, "bad_checksum": 50}
    assert summary["prefixes"] == ["doc4ai_pat_BBBB"]
    assert len([e for e in _events(ctx) if e["id"] == agg["id"]]) >= 2              # live event re-sent on update
    # other IPs are unaffected
    assert g.on_failure("10.0.0.2", "unknown_token", None) is None
    # failures keep extending the block; quiet for a cool-down -> unblocked, counting restarts
    clock.t += 61
    g.sweep()
    assert not g.blocked("10.0.0.1")
    assert g.on_failure("10.0.0.1", "unknown_token", None) is None


def test_guard_memory_is_bounded(ctx):
    clock = Clock()
    g = AuthFailureGuard(CallRecorder(ctx), limit=3, max_ips=100, clock=clock)
    for i in range(500):
        g.on_failure(f"10.1.{i // 256}.{i % 256}", "unknown_token", None)
    assert len(g._ips) <= 100


def test_gate_turns_an_auth_flood_into_one_row_and_429(ctx):
    rec = CallRecorder(ctx)
    guard = AuthFailureGuard(rec, limit=5, window_s=60, cooldown_s=30)
    gate = McpGate(ctx, echo_inner, PatVerifier(ctx.store, SECRET), RateLimiter(lambda: 60), rec, guard=guard)
    attacker = TestClient(gate, base_url="http://127.0.0.1:8765", client=("100.64.0.66", 1))
    statuses = [attacker.post("/mcp", headers={**LIST, "Authorization": "Bearer nope"}, json={"method": "tools/list"}).status_code
                for _ in range(40)]
    assert statuses[:5] == [401] * 5 and set(statuses[5:]) == {429}
    r = attacker.post("/mcp", headers={**LIST, "Authorization": "Bearer nope"}, json={"method": "tools/list"})
    assert r.status_code == 429 and 1 <= int(r.headers["retry-after"]) <= 30 and r.json()["error"] == "too_many_auth_failures"
    r = attacker.post("/mcp?token=x", headers=LIST, json={"method": "tools/list"})      # token-in-URL counts too
    assert r.status_code == 429
    rows = _rows(ctx)
    assert [x["status"] for x in rows].count("auth_error") == 5 and [x["error_code"] for x in rows].count("auth_failures") == 1
    assert len(rows) == 6
    # a valid token from the same address still works during the cool-down
    raw, _tid = _issue(ctx)
    ok = attacker.post("/mcp", headers={**LIST, "Authorization": f"Bearer {raw}"}, json={"method": "tools/list"})
    assert ok.status_code == 200
    # another address is not blocked
    other = TestClient(gate, base_url="http://127.0.0.1:8765", client=("100.64.0.7", 1))
    assert other.post("/mcp", headers={**LIST, "Authorization": "Bearer nope"}, json={"method": "tools/list"}).status_code == 401
    guard.sweep(now=time.time() + 6)
    assert json.loads(next(x for x in _rows(ctx) if x["error_code"] == "auth_failures")["args_summary"])["suppressed"] == 37
