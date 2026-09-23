from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def tmp_root(tmp_path, monkeypatch):
    """Isolated project root + data dir for a test."""
    root = tmp_path / "root"
    (root / "data").mkdir(parents=True)
    (root / "out").mkdir()
    monkeypatch.setenv("AIDOC_ROOT", str(root))
    monkeypatch.setenv("AIDOC_DATA", str(root / "data"))
    monkeypatch.delenv("AIDOC_CONFIG", raising=False)
    monkeypatch.delenv("AIDOC_TOKEN", raising=False)
    return root


@pytest.fixture
def fixtures() -> Path:
    return FIXTURES


# ---- P3 server fixtures (shared by tests/api and tests/unit/test_client.py)

@pytest.fixture
def ctx(tmp_root, monkeypatch):
    from aidoc.config import load_config
    from aidoc.server.app import build_context
    from tests.fakes.scenario import fake_env, write_scenario
    fake_env(monkeypatch, write_scenario(tmp_root / "sc.json"))
    c = build_context(load_config(), token=None, start_workers=False)
    yield c
    c.close()


@pytest.fixture
def client(ctx):
    from fastapi.testclient import TestClient

    from aidoc.server.app import create_app
    with TestClient(create_app(ctx), client=("127.0.0.1", 50000)) as c:     # a loopback client
        yield c


@pytest.fixture
def token_ctx(tmp_root, monkeypatch):
    from aidoc.config import load_config
    from aidoc.server.app import build_context
    from tests.fakes.scenario import fake_env, write_scenario
    fake_env(monkeypatch, write_scenario(tmp_root / "sc.json"))
    c = build_context(load_config(), token="s3cret", start_workers=False)
    yield c
    c.close()


@pytest.fixture
def live_server(ctx):
    """A real uvicorn server (ephemeral port) in a thread: Starlette's TestClient buffers whole response bodies,
    so it cannot read an endless SSE stream. Yields the base URL."""
    import threading
    import time as _time

    import uvicorn

    from aidoc.server.app import create_app
    server = uvicorn.Server(uvicorn.Config(create_app(ctx), host="127.0.0.1", port=0, log_level="warning",
                                           lifespan="off"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    deadline = _time.time() + 10
    while not server.started:
        if _time.time() > deadline:
            raise RuntimeError("uvicorn did not start")
        _time.sleep(0.02)
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    th.join(10)
