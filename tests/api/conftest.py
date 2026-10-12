"""API test helpers: a client whose server runs on a given aidoc.toml (VLM plan Task 10)."""
from __future__ import annotations

import time

import pytest


class _Client:
    """A TestClient plus `wait_event(kind, pred)` over the server's event log."""

    def __init__(self, tc, ctx):
        self._tc, self.ctx = tc, ctx
        self.app = tc.app
        events = ctx.store.events_since(0)
        self.since = events[-1]["seq"] if events else 0      # only events of this client's server count

    def __getattr__(self, name):
        return getattr(self._tc, name)

    def wait_event(self, kind: str, pred=lambda p: True, timeout: float = 10.0, since: int | None = None) -> dict:
        since = self.since if since is None else since
        deadline = time.time() + timeout
        while time.time() < deadline:
            for e in self.ctx.store.events_since(since):
                if e["kind"] == kind and pred(e["payload"]):
                    return e["payload"]
            time.sleep(0.05)
        raise AssertionError(f"no {kind} event within {timeout}s")


@pytest.fixture
def client_factory(tmp_root, monkeypatch):
    from fastapi.testclient import TestClient

    from aidoc.config import load_config
    from aidoc.server.app import build_context, create_app
    from tests.fakes.scenario import fake_env, write_scenario
    fake_env(monkeypatch, write_scenario(tmp_root / "sc.json"))
    opened = []

    def make(toml: str = "") -> _Client:
        (tmp_root / "aidoc.toml").write_text(toml, encoding="utf-8")
        ctx = build_context(load_config(), token=None, start_workers=False)
        tc = TestClient(create_app(ctx), client=("127.0.0.1", 50000), base_url="http://127.0.0.1:8765")
        tc.__enter__()
        opened.append((tc, ctx))
        return _Client(tc, ctx)

    yield make
    for tc, ctx in opened:
        tc.__exit__(None, None, None)
        ctx.close()
