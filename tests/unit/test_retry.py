import shutil
import threading
import time

import pytest

from aidoc.config import load_config
from aidoc.engines.base import EngineError
from aidoc.engines.docling import DoclingEngine
from aidoc.engines.mineru import MineruEngine
from aidoc.engines.registry import get_engines
from aidoc.models import ConvertOptions, ErrorKind, TaskStatus
from aidoc.names import file_sha256
from aidoc.pipeline import run_task
from aidoc.retry import RetryPolicy
from aidoc.store import Store
from tests.fakes.scenario import fake_env, write_scenario

P = RetryPolicy()


def test_input_fails():
    assert P.decide(EngineError(ErrorKind.input, "x"), 0, DoclingEngine(), {}).action == "fail"


def test_engine_falls_back():
    assert P.decide(EngineError(ErrorKind.engine, "x"), 0, DoclingEngine(), {}).action == "fallback"


def test_transient_backoff():
    d0 = P.decide(EngineError(ErrorKind.transient, "timeout"), 0, DoclingEngine(), {"page_batch_size": 16})
    d1 = P.decide(EngineError(ErrorKind.transient, "timeout"), 1, DoclingEngine(), {"page_batch_size": 16})
    d2 = P.decide(EngineError(ErrorKind.transient, "timeout"), 2, DoclingEngine(), {"page_batch_size": 16})
    assert (d0.action, d0.delay_s) == ("retry", 5.0) and (d1.action, d1.delay_s) == ("retry", 30.0)
    assert d2.action == "fallback"


def test_oom_downgrades_then_fallback():
    d = P.decide(EngineError(ErrorKind.transient, "oom", oom=True), 0, MineruEngine(), {"tier": "standard"})
    assert d.action == "retry" and d.engine_opts == {"tier": "basic"} and d.delay_s == 0
    d = P.decide(EngineError(ErrorKind.transient, "oom", oom=True), 0, MineruEngine(), {"tier": "basic"})
    assert d.action == "fallback"
    d = P.decide(EngineError(ErrorKind.transient, "oom", oom=True), 0, DoclingEngine(), {"page_batch_size": 16})
    assert d.engine_opts == {"page_batch_size": 8}


@pytest.fixture
def env(tmp_root, monkeypatch, fixtures):
    sc = write_scenario(tmp_root / "sc.json"); fake_env(monkeypatch, sc)
    cfg = load_config(); store = Store(tmp_root / "data" / "aidoc.db")
    monkeypatch.setattr("aidoc.pipeline.RETRY_POLICY", RetryPolicy(max_transient=2, backoff=(0.05, 0.1)))

    def make(name="text.pdf", **opt):
        src = tmp_root / name; shutil.copy(fixtures / name, src)
        opts = ConvertOptions(output_dir=tmp_root / "out", **opt); job = store.create_job(opts, "cli")
        return store.create_task(job, str(src), file_sha256(src), src.stat().st_size, src.stat().st_mtime, opts.lang,
                                 str(tmp_root / "out" / src.stem))[0]
    return {"cfg": cfg, "store": store, "sc": sc, "make": make, "root": tmp_root, "engines": lambda: get_engines(cfg)}


def test_pipeline_retries_transient_then_succeeds(env):
    write_scenario(env["sc"], rules=[{"match": {"engine": "docling", "attempt": 1}, "behavior": "crash"}])
    tid = env["make"]()
    assert run_task(env["store"], tid, env["engines"](), env["cfg"]) == TaskStatus.done
    t = env["store"].get_task(tid)
    assert t["engine"] == "docling" and t["attempt"] == 2 and t["tried"][0]["error_kind"] == "transient"


def test_pipeline_oom_downgrade_recorded(env):
    write_scenario(env["sc"], rules=[{"match": {"engine": "mineru", "attempt": 1}, "behavior": "oom"}])
    tid = env["make"]("scanned_cht.pdf", mineru_tier="standard")
    assert run_task(env["store"], tid, env["engines"](), env["cfg"]) == TaskStatus.done
    t = env["store"].get_task(tid)
    assert t["engine"] == "mineru" and "oom" in t["tried"][0]["error_msg"].lower()
    assert "basic" in t["tried"][0]["error_msg"]


def test_pipeline_exhausts_retries_then_fallback(env):
    write_scenario(env["sc"], rules=[{"match": {"engine": "docling"}, "behavior": "crash"}])
    tid = env["make"]()
    assert run_task(env["store"], tid, env["engines"](), env["cfg"]) == TaskStatus.done
    t = env["store"].get_task(tid)
    assert [a["engine"] for a in t["tried"]] == ["docling", "docling", "docling", "mineru"] and t["engine"] == "mineru"


def test_timeout_is_transient(env):
    write_scenario(env["sc"], rules=[{"match": {"engine": "docling", "attempt": 1}, "behavior": "timeout"}])
    tid = env["make"](timeout_s=1)
    assert run_task(env["store"], tid, env["engines"](), env["cfg"]) == TaskStatus.done
    assert "timeout" in env["store"].get_task(tid)["tried"][0]["error_msg"]


def test_cancel_during_backoff(env, monkeypatch):
    monkeypatch.setattr("aidoc.pipeline.RETRY_POLICY", RetryPolicy(max_transient=2, backoff=(30.0, 30.0)))
    write_scenario(env["sc"], rules=[{"match": {"engine": "docling"}, "behavior": "crash"}])
    tid = env["make"](); ev = threading.Event(); set_at = []

    def cancel_in_backoff():                  # Review Focus 4: set cancel while the 30 s backoff is running
        while not (env["store"].get_task(tid) or {}).get("tried"):
            time.sleep(0.02)
        time.sleep(0.2)
        set_at.append(time.time()); ev.set()
    threading.Thread(target=cancel_in_backoff, daemon=True).start()
    assert run_task(env["store"], tid, env["engines"](), env["cfg"], cancel=ev) == TaskStatus.cancelled
    assert set_at and time.time() - set_at[0] < 1.0
    assert len(env["store"].get_task(tid)["tried"]) == 1       # no second attempt started


def test_input_error_from_engine_fails_without_retry(env):
    write_scenario(env["sc"], rules=[{"match": {"engine": "docling"}, "behavior": "input_error"}])
    tid = env["make"]()
    assert run_task(env["store"], tid, env["engines"](), env["cfg"]) == TaskStatus.failed
    t = env["store"].get_task(tid)
    assert t["error_kind"] == "input" and len(t["tried"]) == 1
