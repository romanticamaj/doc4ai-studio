import json
import shutil
from pathlib import Path
import pytest
from aidoc.config import load_config
from aidoc.engines.registry import get_engines
from aidoc.models import ConvertOptions, TaskStatus
from aidoc.names import file_sha256
from aidoc.pipeline import run_task
from aidoc.store import Store
from tests.fakes.scenario import write_scenario, fake_env


@pytest.fixture
def env(tmp_root, monkeypatch, fixtures):
    sc = write_scenario(tmp_root / "sc.json")
    fake_env(monkeypatch, sc)
    cfg = load_config()
    store = Store(tmp_root / "data" / "aidoc.db")

    def make_task(name, out_name=None, **opt):
        src = tmp_root / (out_name or name)
        shutil.copy(fixtures / name, src)
        opts = ConvertOptions(output_dir=tmp_root / "out", **opt)
        job = store.create_job(opts, "cli")
        tid, _ = store.create_task(job, str(src), file_sha256(src), src.stat().st_size, src.stat().st_mtime, opts.lang,
                                   str(tmp_root / "out" / Path(src).stem))
        return tid
    yield dict(cfg=cfg, store=store, sc=sc, make=make_task, root=tmp_root)
    store.close()


def test_happy_path_docling(env):
    tid = env["make"]("text.pdf")
    events = []
    st = run_task(env["store"], tid, get_engines(env["cfg"]), env["cfg"], emit=lambda k, p: events.append(k))
    assert st == TaskStatus.done
    t = env["store"].get_task(tid)
    assert t["engine"] == "docling" and t["quality"]["level"] == "ok"
    out = env["root"] / "out" / "text"
    assert (out / "text.md").exists() and (out / "assets" / "p1_1.png").exists()
    sc = json.loads((out / "text.json").read_text(encoding="utf-8"))
    assert sc["engine"] == "docling" and sc["tried"][0]["engine"] == "docling" and sc["pages"] == 3 and sc["segments"] == 1
    assert env["store"].find_document(t["sha256"], str(out))["status"] == "ok"
    assert "task.updated" in events and not (env["root"] / "out" / ".tmp" / tid).exists()


def test_fallback_on_low_quality(env):
    write_scenario(env["sc"], rules=[{"match": {"engine": "docling"}, "behavior": "low"}])
    tid = env["make"]("text.pdf")
    assert run_task(env["store"], tid, get_engines(env["cfg"]), env["cfg"]) == TaskStatus.done
    t = env["store"].get_task(tid)
    assert t["engine"] == "mineru" and [a["engine"] for a in t["tried"]] == ["docling", "mineru"] and t["tried"][0]["score"] < 0.5


def test_all_low_keeps_best(env):
    write_scenario(env["sc"], default="low")
    tid = env["make"]("text.pdf")
    assert run_task(env["store"], tid, get_engines(env["cfg"]), env["cfg"]) == TaskStatus.low
    assert env["store"].get_task(tid)["quality"]["level"] == "low" and (env["root"] / "out" / "text" / "text.md").exists()


def test_engine_error_then_fallback(env):
    write_scenario(env["sc"], rules=[{"match": {"engine": "docling"}, "behavior": "error"}])
    tid = env["make"]("text.pdf")
    assert run_task(env["store"], tid, get_engines(env["cfg"]), env["cfg"]) == TaskStatus.done
    assert env["store"].get_task(tid)["tried"][0]["error_kind"] == "engine"


def test_input_errors(env):
    for name, code in [("encrypted.pdf", "encrypted"), ("corrupt.pdf", "corrupt"), ("bad.exe", "unsupported_type")]:
        tid = env["make"](name)
        assert run_task(env["store"], tid, get_engines(env["cfg"]), env["cfg"]) == TaskStatus.failed
        t = env["store"].get_task(tid)
        assert t["error_kind"] == "input" and code in t["error_msg"]


def test_forced_unsupported(env):
    tid = env["make"]("sample.docx", engine="mineru")
    assert run_task(env["store"], tid, get_engines(env["cfg"]), env["cfg"]) == TaskStatus.failed
    assert "engine_unsupported" in env["store"].get_task(tid)["error_msg"]


def test_cache_skip_and_force_and_retry_low(env):
    tid = env["make"]("text.pdf")
    run_task(env["store"], tid, get_engines(env["cfg"]), env["cfg"])
    tid2 = env["make"]("text.pdf")
    assert run_task(env["store"], tid2, get_engines(env["cfg"]), env["cfg"]) == TaskStatus.skipped
    tid3 = env["make"]("text.pdf", force=True)
    assert run_task(env["store"], tid3, get_engines(env["cfg"]), env["cfg"]) == TaskStatus.done
    write_scenario(env["sc"], default="low")
    tid4 = env["make"]("sample.docx")
    assert run_task(env["store"], tid4, get_engines(env["cfg"]), env["cfg"]) == TaskStatus.low
    tid5 = env["make"]("sample.docx")
    assert run_task(env["store"], tid5, get_engines(env["cfg"]), env["cfg"]) == TaskStatus.skipped
    write_scenario(env["sc"])
    tid6 = env["make"]("sample.docx", retry_low=True)
    assert run_task(env["store"], tid6, get_engines(env["cfg"]), env["cfg"]) == TaskStatus.done


def test_cjk_paths(env, fixtures):
    tid = env["make"]("text.pdf", out_name="測試 報告.pdf")
    assert run_task(env["store"], tid, get_engines(env["cfg"]), env["cfg"]) == TaskStatus.done
    assert (env["root"] / "out" / "測試 報告" / "測試 報告.md").exists()


def test_same_stem_different_content(env):
    t1 = env["make"]("text.pdf", out_name="report.pdf")
    t2 = env["make"]("sample.docx", out_name="report.docx")
    for t in (t1, t2):
        assert run_task(env["store"], t, get_engines(env["cfg"]), env["cfg"]) == TaskStatus.done
    dirs = sorted(p.name for p in (env["root"] / "out").iterdir() if p.name.startswith("report"))
    assert len(dirs) == 2 and dirs[0] == "report"


def test_no_engine_available(env, monkeypatch):
    engines = get_engines(env["cfg"])
    for e in engines.values():
        monkeypatch.setattr(e, "available", lambda: False)
    tid = env["make"]("text.pdf")
    assert run_task(env["store"], tid, engines, env["cfg"]) == TaskStatus.failed
    assert "aidoc setup" in env["store"].get_task(tid)["error_msg"]
