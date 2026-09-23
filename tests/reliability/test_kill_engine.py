import shutil
import threading
import time

from aidoc import procs
from aidoc.config import load_config
from aidoc.engines.registry import get_engines
from aidoc.models import ConvertOptions, TaskStatus
from aidoc.names import file_sha256
from aidoc.pipeline import run_task
from aidoc.retry import RetryPolicy
from aidoc.store import Store
from tests.fakes.scenario import fake_env, write_scenario


def test_killed_engine_is_transient_and_retried(tmp_root, fixtures, monkeypatch):
    fake_env(monkeypatch, write_scenario(tmp_root / "sc.json", rules=[{"match": {"attempt": 1}, "behavior": "slow_ok"}],
                                         slow_s=20))
    monkeypatch.setattr("aidoc.pipeline.RETRY_POLICY", RetryPolicy(backoff=(0.1, 0.1)))
    cfg = load_config(); store = Store(tmp_root / "data" / "aidoc.db")
    src = tmp_root / "a.pdf"; shutil.copy(fixtures / "text.pdf", src)
    job = store.create_job(ConvertOptions(output_dir=tmp_root / "out"), "cli")
    tid, _ = store.create_task(job, str(src), file_sha256(src), 1, 1.0, "cht", str(tmp_root / "out" / "a"))

    def killer():
        while not (store.get_task(tid) or {}).get("pid"):
            time.sleep(0.05)
        time.sleep(0.5); procs.kill_tree(store.get_task(tid)["pid"])
    threading.Thread(target=killer, daemon=True).start()
    t0 = time.time()
    assert run_task(store, tid, get_engines(cfg), cfg) == TaskStatus.done
    assert time.time() - t0 < 15
    t = store.get_task(tid)
    assert t["tried"][0]["error_kind"] == "transient" and t["attempt"] == 2 and t["engine"] == "docling"
