import sys
import time
from pathlib import Path
import pytest
from aidoc.engines.host import RunnerHost, compute_timeout, runner_env
from aidoc.engines.base import EngineError
from aidoc.models import ConvertOptions
from aidoc.config import load_config
from aidoc import paths
from tests.fakes.scenario import write_scenario


def make_host(tmp_path, monkeypatch, **sc):
    monkeypatch.setenv("AIDOC_FAKE_SCENARIO", str(write_scenario(tmp_path / "sc.json", **sc)))
    return RunnerHost(Path(sys.executable), paths.runner_script("fake"), {}, startup_timeout_s=30)


def req(tmp_path, name="a.pdf"):
    return {"src": str(tmp_path / name), "out_dir": str(tmp_path / "raw"), "lang": "cht",
            "engine_opts": {"fake_engine": "docling"}, "kind": "pdf"}


def test_ok_with_progress(tmp_path, monkeypatch):
    h = make_host(tmp_path, monkeypatch)
    h.start(tmp_path)
    prog = []
    res = h.run(req(tmp_path), tmp_path / "wd 中文 dir", 30, lambda f, line: prog.append((f, line)))
    h.close()
    assert Path(res["markdown_path"]).read_text(encoding="utf-8").startswith("<!-- page: 1 -->")
    assert res["page_count"] == 3 and (tmp_path / "wd 中文 dir" / "request.json").exists()
    assert [p[0] for p in prog if p[1] == ""] == pytest.approx([1/3, 2/3, 1.0])


def test_engine_error(tmp_path, monkeypatch):
    h = make_host(tmp_path, monkeypatch, default="error")
    h.start(tmp_path)
    with pytest.raises(EngineError) as ei:
        h.run(req(tmp_path), tmp_path / "wd", 30, lambda f, line: None)
    assert ei.value.kind.value == "engine" and h.alive()
    h.close()


def test_oom_detected(tmp_path, monkeypatch):
    h = make_host(tmp_path, monkeypatch, default="oom")
    h.start(tmp_path)
    with pytest.raises(EngineError) as ei:
        h.run(req(tmp_path), tmp_path / "wd", 30, lambda f, line: None)
    assert ei.value.kind.value == "transient" and ei.value.oom


def test_timeout_kills(tmp_path, monkeypatch):
    h = make_host(tmp_path, monkeypatch, default="timeout")
    h.start(tmp_path)
    t0 = time.time()
    with pytest.raises(EngineError) as ei:
        h.run(req(tmp_path), tmp_path / "wd", 2, lambda f, line: None)
    assert ei.value.kind.value == "transient" and "timeout" in str(ei.value) and time.time() - t0 < 15
    assert not h.alive()


def test_crash_is_transient(tmp_path, monkeypatch):
    h = make_host(tmp_path, monkeypatch, default="crash")
    h.start(tmp_path)
    with pytest.raises(EngineError) as ei:
        h.run(req(tmp_path), tmp_path / "wd", 30, lambda f, line: None)
    assert ei.value.kind.value == "transient" and not ei.value.oom


def test_compute_timeout(tmp_root):
    lim = load_config().limits
    o = ConvertOptions(output_dir=Path("o"))
    assert compute_timeout(1, 0, o, lim) == 120 and compute_timeout(40, 0, o, lim) == 2400
    assert compute_timeout(None, 10 * 2**20, o, lim) == 300 and compute_timeout(None, 2**30, o, lim) == 1800
    assert compute_timeout(40, 0, ConvertOptions(output_dir=Path("o"), timeout_s=7), lim) == 7


def test_runner_env(tmp_root):
    env = runner_env(tmp_root / "data")
    assert env["PYTHONUTF8"] == "1" and env["PYTHONIOENCODING"] == "utf-8"
    assert env["MINERU_HOME"].endswith("mineru") and env["MINERU_MODEL_SMALL_BACKEND"] == "torch"
    assert env["HF_HOME"].endswith("hf") and env["EASYOCR_MODULE_PATH"].endswith("easyocr")
