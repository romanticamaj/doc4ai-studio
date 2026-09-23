import json
import sys
import threading
import time
from pathlib import Path

import psutil
import pytest

from aidoc import paths
from aidoc.config import load_config
from aidoc.engines.base import EngineCancelled, EngineError
from aidoc.engines.docling import DoclingEngine
from aidoc.engines.host import RunnerHost
from aidoc.engines.mineru import MineruEngine
from aidoc.engines.registry import get_engines
from aidoc.models import ConvertOptions, ErrorKind
from aidoc.probe import probe_file
from aidoc.segment import split_pdf
from tests.fakes.scenario import fake_env, write_scenario


def test_session_reuses_one_process(tmp_root, fixtures, monkeypatch):
    sc = write_scenario(tmp_root / "sc.json"); fake_env(monkeypatch, sc)
    e = get_engines(load_config())["docling"]; pr = probe_file(fixtures / "big.pdf")
    s = e.open_session(ConvertOptions(output_dir=tmp_root / "out"), pr)
    pid = s.pid; assert pid
    a = s.convert(split_pdf(fixtures / "big.pdf", 1, 40, tmp_root / "s0.pdf"), tmp_root / "seg_0", (1, 40),
                  lambda f, l: None, timeout_s=60, segment_idx=0)
    b = s.convert(split_pdf(fixtures / "big.pdf", 41, 45, tmp_root / "s1.pdf"), tmp_root / "seg_1", (41, 45),
                  lambda f, l: None, timeout_s=60, segment_idx=1)
    assert s.pid == pid and a.page_count == 40 and b.page_count == 5
    s.close()
    calls = [json.loads(l) for l in (tmp_root / "calls.jsonl").read_text().splitlines()]
    assert [c["pages"] for c in calls] == [[1, 40], [41, 45]]


def test_cancel_kills_runner(tmp_root, fixtures, monkeypatch):
    fake_env(monkeypatch, write_scenario(tmp_root / "sc.json", default="slow_ok", slow_s=30))
    e = get_engines(load_config())["mineru"]; pr = probe_file(fixtures / "text.pdf")
    s = e.open_session(ConvertOptions(output_dir=tmp_root / "out"), pr); ev = threading.Event()
    threading.Timer(0.5, ev.set).start(); t0 = time.time()
    with pytest.raises(EngineCancelled):
        s.convert(fixtures / "text.pdf", tmp_root / "seg_0", None, lambda f, l: None, timeout_s=60, cancel=ev)
    assert time.time() - t0 < 5
    assert not psutil.pid_exists(s.pid) or psutil.Process(s.pid).status() == psutil.STATUS_ZOMBIE


def test_oom_downgrade_rules():
    assert DoclingEngine().oom_downgrade({"page_batch_size": 16}) == {"page_batch_size": 8}
    assert DoclingEngine().oom_downgrade({"page_batch_size": 1}) is None
    assert MineruEngine().oom_downgrade({"tier": "standard"}) == {"tier": "basic"}
    assert MineruEngine().oom_downgrade({"tier": "basic"}) is None


def test_fake_engine_follows_real_engine_opts_and_downgrade(tmp_root, fixtures, monkeypatch):
    fake_env(monkeypatch, write_scenario(tmp_root / "sc.json"))
    engines = get_engines(load_config()); pr = probe_file(fixtures / "text.pdf")
    eo = engines["mineru"].engine_opts(ConvertOptions(output_dir=tmp_root, mineru_tier="standard"), pr)
    assert eo["fake_engine"] == "mineru" and eo["tier"] == "standard"
    assert engines["mineru"].oom_downgrade(eo)["tier"] == "basic"
    assert engines["docling"].oom_downgrade({"page_batch_size": 4, "fake_engine": "docling"})["page_batch_size"] == 2


def test_session_survives_engine_error(tmp_root, fixtures, monkeypatch):
    fake_env(monkeypatch, write_scenario(tmp_root / "sc.json",
                                         rules=[{"match": {"segment_idx": 0}, "behavior": "error"}]))
    e = get_engines(load_config())["docling"]; pr = probe_file(fixtures / "text.pdf")
    s = e.open_session(ConvertOptions(output_dir=tmp_root / "out"), pr)
    with pytest.raises(EngineError) as ei:
        s.convert(fixtures / "text.pdf", tmp_root / "a", None, lambda f, l: None, timeout_s=60, segment_idx=0)
    assert ei.value.kind == ErrorKind.engine
    r = s.convert(fixtures / "text.pdf", tmp_root / "b", None, lambda f, l: None, timeout_s=60, segment_idx=1)
    assert r.page_count == 3
    s.close()


def test_unknown_runner_error_kind_is_engine_error(tmp_path, monkeypatch):
    """P1 deferred: an AIDOC_ERROR with an unknown kind must not raise ValueError."""
    monkeypatch.setenv("AIDOC_FAKE_SCENARIO", str(write_scenario(tmp_path / "sc.json", default="bad_kind")))
    h = RunnerHost(Path(sys.executable), paths.runner_script("fake"), {}, startup_timeout_s=30)
    h.start(tmp_path)
    req = {"src": str(tmp_path / "a.pdf"), "out_dir": str(tmp_path / "raw"), "lang": "cht",
           "engine_opts": {"fake_engine": "docling"}, "kind": "pdf"}
    try:
        with pytest.raises(EngineError) as ei:
            h.run(req, tmp_path / "wd", 30, lambda f, l: None)
    finally:
        h.close()
    assert ei.value.kind == ErrorKind.engine and "weird" in ei.value.message


def test_fake_slow_ok_sleeps_delay_s_only(tmp_root, fixtures, monkeypatch):
    """P1 deferred: index §6 says slow_ok sleeps delay_s (no hidden extra 5 s)."""
    fake_env(monkeypatch, write_scenario(tmp_root / "sc.json", default="slow_ok", delay_s=0.3))
    e = get_engines(load_config())["docling"]; pr = probe_file(fixtures / "text.pdf")
    t0 = time.time()
    e.convert(fixtures / "text.pdf", tmp_root / "w", ConvertOptions(output_dir=tmp_root), None, lambda f, l: None,
              timeout_s=60, probe=pr)
    assert 0.3 <= time.time() - t0 < 3.5
