"""P2 deferred M12: Ctrl+C / Ctrl+Break in an in-process `aidoc batch` ends cleanly: runner killed, task cancelled
(resumable), exit code 130, a short message and no traceback."""
import os
import shutil
import signal
import subprocess
import sys

import psutil
import pytest

from aidoc import procs
from tests.fakes.scenario import write_scenario

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="CTRL_BREAK_EVENT is Windows-only")


def test_ctrl_break_in_batch_exits_130_without_traceback(tmp_root, fixtures, wait_for, db):
    d = tmp_root / "in"
    d.mkdir()
    shutil.copy(fixtures / "big.pdf", d / "big.pdf")
    shutil.copy(fixtures / "text.pdf", d / "text.pdf")
    sc = write_scenario(tmp_root / "sc.json", rules=[{"match": {"segment_idx": 1}, "behavior": "slow_ok"}], slow_s=30)
    env = {**os.environ, "AIDOC_ROOT": str(tmp_root), "AIDOC_DATA": str(tmp_root / "data"), "AIDOC_FAKE_ENGINES": "1",
           "AIDOC_FAKE_SCENARIO": str(sc), "PYTHONUTF8": "1"}
    env.pop("AIDOC_CONFIG", None)
    p = subprocess.Popen([sys.executable, "-m", "aidoc.cli", "batch", str(d), "-o", str(tmp_root / "out")],
                         env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    try:
        store = db(tmp_root)

        def big():
            return next(t for t in store.list_tasks() if t["source_path"].endswith("big.pdf"))
        wait_for(lambda: [s["status"] for s in store.list_segments(big()["id"])] == ["done", "converting"]
                 and big()["pid"], 60)
        runner = big()["pid"]
        os.kill(p.pid, signal.CTRL_BREAK_EVENT)
        out, err = p.communicate(timeout=30)
    finally:
        if p.poll() is None:
            procs.kill_tree(p.pid)
    err = err.decode("utf-8", "replace")
    assert p.returncode == 130, err
    assert "Traceback" not in err and "interrupted" in err
    assert not psutil.pid_exists(runner) or not procs.is_aidoc_runner(runner)
    t = big()
    assert t["status"] == "cancelled" and t["pid"] is None
    assert [s["status"] for s in store.list_segments(t["id"])] == ["done", "queued"]
    others = [x for x in store.list_tasks() if x["id"] != t["id"]]
    assert all(x["status"] in ("done", "cancelled") for x in others)       # nothing left queued
    assert store.get_job(t["job_id"])["status"] != "running"
