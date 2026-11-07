from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
REPO = Path(__file__).resolve().parents[1]


# ---- Doc4AI folder isolation (spec 2026-10-29 §12.4): no test may write into the repo's source/, the platform
# default Doc4AI folder or the Doc4AI folder the repo's doc4ai.toml names. Every test gets its own
# DOC4AI_LIBRARY_ROOT (tests of the resolution rules opt out with @pytest.mark.library_root_env), and the session
# fails when any of those folders changed (or was created).

@pytest.fixture(autouse=True)
def _isolated_library_root(request, tmp_path, monkeypatch):
    if request.node.get_closest_marker("library_root_env") is None:
        monkeypatch.setenv("DOC4AI_LIBRARY_ROOT", str(tmp_path / "library"))


def _guarded_source_dirs() -> list[Path]:
    from doc4ai.config import default_library_root
    # the repo's source/, the platform default Doc4AI folder (C:\doc4ai / ~/doc4ai, user decision 2026-10-29: it
    # must not even be created by a test) and the root_dir the repo's doc4ai.toml names
    dirs = [REPO / "source", default_library_root()]
    try:
        if sys.version_info >= (3, 11):
            import tomllib
        else:  # pragma: no cover
            import tomli as tomllib
        with open(REPO / "doc4ai.toml", "rb") as f:
            rd = ((tomllib.load(f).get("general") or {}).get("root_dir") or "").strip()
        if rd:
            dirs.append(Path(rd) / "source")
    except (OSError, ValueError):
        pass
    return dirs


def _snapshot(d: Path) -> set[tuple[str, int, int]]:
    out = set()
    if not d.exists():
        return out
    out.add(("<exists>", 0, 0))                    # creating an empty folder counts as a change too
    for dirpath, dirs, files in os.walk(d):
        out.update((("<dir>/" + (Path(dirpath) / n).relative_to(d).as_posix()), 0, 0) for n in dirs)
        for name in files:
            p = Path(dirpath) / name
            try:
                st = p.stat()
            except OSError:
                continue
            out.add((p.relative_to(d).as_posix(), st.st_size, st.st_mtime_ns))
    return out


def pytest_sessionstart(session):
    session.config._doc4ai_source_guard = {str(d): _snapshot(d) for d in _guarded_source_dirs()}


def pytest_sessionfinish(session, exitstatus):
    before = getattr(session.config, "_doc4ai_source_guard", None)
    if before is None:
        return
    changed = [d for d, snap in before.items() if _snapshot(Path(d)) != snap]
    if changed:
        msg = f"\nSOURCE-COPY ISOLATION GUARD: a test changed {', '.join(changed)} (spec 2026-10-29 §12.4)\n"
        sys.stderr.write(msg)
        tr = session.config.pluginmanager.get_plugin("terminalreporter")
        if tr is not None:
            tr.write_line(msg.strip(), red=True)
        session.exitstatus = 1


@pytest.fixture
def tmp_root(tmp_path, monkeypatch):
    """Isolated project root + data dir for a test; its Doc4AI folder is the root itself (out/ and source/)."""
    root = tmp_path / "root"
    (root / "data").mkdir(parents=True)
    (root / "out").mkdir()
    monkeypatch.setenv("DOC4AI_ROOT", str(root))
    monkeypatch.setenv("DOC4AI_LIBRARY_ROOT", str(root))
    monkeypatch.setenv("DOC4AI_DATA", str(root / "data"))
    monkeypatch.delenv("DOC4AI_CONFIG", raising=False)
    monkeypatch.delenv("DOC4AI_TOKEN", raising=False)
    return root


@pytest.fixture
def fixtures() -> Path:
    return FIXTURES


# ---- P3 server fixtures (shared by tests/api and tests/unit/test_client.py)

@pytest.fixture
def ctx(tmp_root, monkeypatch):
    from doc4ai.config import load_config
    from doc4ai.server.app import build_context
    from tests.fakes.scenario import fake_env, write_scenario
    fake_env(monkeypatch, write_scenario(tmp_root / "sc.json"))
    c = build_context(load_config(), token=None, start_workers=False)
    yield c
    c.close()


@pytest.fixture
def client(ctx):
    from fastapi.testclient import TestClient

    from doc4ai.server.app import create_app
    with TestClient(create_app(ctx), client=("127.0.0.1", 50000),            # a loopback client
                    base_url="http://127.0.0.1:8765") as c:
        yield c


@pytest.fixture
def token_ctx(tmp_root, monkeypatch):
    from doc4ai.config import load_config
    from doc4ai.server.app import build_context
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

    from doc4ai.server.app import create_app
    server = uvicorn.Server(uvicorn.Config(create_app(ctx), host="127.0.0.1", port=0, log_level="warning",
                                           lifespan="on"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    deadline = _time.time() + 10
    while not server.started:
        if _time.time() > deadline:
            raise RuntimeError("uvicorn did not start")
        _time.sleep(0.02)
    port = server.servers[0].sockets[0].getsockname()[1]
    ctx.extras["bind_port"] = port                    # like `doc4ai serve`: the port it really listens on
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    th.join(10)


@pytest.fixture(autouse=True)
def _oauth_mode(request):
    """DOC4AI_TEST_OAUTH=0|1 runs a suite (tests/mcp, tests/compat) with [oauth] enabled forced off or on, so the PAT
    path is checked in both modes (local OAuth plan 2026-11-05, matrix M3). Unset: the config default (on)."""
    flag = os.environ.get("DOC4AI_TEST_OAUTH")
    if flag not in ("0", "1"):
        return
    for name in ("ctx", "token_ctx"):
        if name in request.fixturenames:
            request.getfixturevalue(name).config.oauth.enabled = flag == "1"
