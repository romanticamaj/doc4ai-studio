"""Final review I1: a CUDA OOM raised inside the runner (process stays alive) must reach the retry policy as OOM."""
import shutil
import sys
from pathlib import Path

import pytest

from aidoc import paths
from aidoc.config import load_config
from aidoc.engines.base import EngineError
from aidoc.engines.host import RunnerHost
from aidoc.engines.registry import get_engines
from aidoc.models import ConvertOptions, TaskStatus
from aidoc.names import file_sha256
from aidoc.pipeline import run_task
from aidoc.store import Store
from tests.fakes.scenario import fake_env, write_scenario


def test_in_process_oom_is_transient_oom_and_runner_survives(tmp_path, monkeypatch):
    monkeypatch.setenv("AIDOC_FAKE_SCENARIO", str(write_scenario(tmp_path / "sc.json", default="oom_raise")))
    h = RunnerHost(Path(sys.executable), paths.runner_script("fake"), {}, startup_timeout_s=30)
    h.start(tmp_path)
    req = {"src": str(tmp_path / "a.pdf"), "out_dir": str(tmp_path / "raw"), "lang": "cht",
           "engine_opts": {"fake_engine": "mineru"}, "kind": "pdf"}
    try:
        with pytest.raises(EngineError) as ei:
            h.run(req, tmp_path / "wd", 30, lambda f, line: None)
        assert ei.value.kind.value == "transient" and ei.value.oom and h.alive()
    finally:
        h.close()


def test_pipeline_downgrades_on_in_process_oom(tmp_root, fixtures, monkeypatch):
    fake_env(monkeypatch, write_scenario(tmp_root / "sc.json",
                                         rules=[{"match": {"engine": "mineru", "attempt": 1}, "behavior": "oom_raise"}]))
    cfg = load_config(); store = Store(tmp_root / "data" / "aidoc.db")
    src = tmp_root / "scanned_cht.pdf"; shutil.copy(fixtures / "scanned_cht.pdf", src)
    opts = ConvertOptions(output_dir=tmp_root / "out", mineru_tier="standard"); job = store.create_job(opts, "cli")
    tid, _ = store.create_task(job, str(src), file_sha256(src), 1, 1.0, "cht", str(tmp_root / "out" / "scanned_cht"))
    assert run_task(store, tid, get_engines(cfg), cfg) == TaskStatus.done
    t = store.get_task(tid)
    assert t["engine"] == "mineru" and t["tried"][0]["error_msg"].startswith("oom: retrying with {'tier': 'basic'}")
