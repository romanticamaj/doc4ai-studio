import os
import subprocess
import sys

import psutil

from aidoc import paths
from aidoc.config import load_config
from aidoc.models import ConvertOptions
from aidoc.recovery import recover_on_startup
from aidoc.store import Store
from aidoc.tasklock import TaskLock, lock_path

_counter = [0]


def mk(store, root, status, pid=None, segs=()):
    """Create a task row in the given status; each call gets a unique sha256 and output_dir."""
    _counter[0] += 1
    job = store.create_job(ConvertOptions(output_dir=root / "out"), "cli")
    tid, _ = store.create_task(job, "a.pdf", os.urandom(32).hex(), 1, 1.0, "cht", str(root / "out" / f"d{_counter[0]}"))
    store.update_task(tid, status=status, pid=pid)
    if segs:
        ids = store.create_segments(tid, [(1, 40), (41, 45)])
        for i, s in zip(ids, segs):
            store.update_segment(i, status=s, output_path="p" if s != "queued" else None)
    return tid


def test_orphan_runner_killed_but_unrelated_pid_spared(tmp_root):
    store = Store(tmp_root / "data" / "aidoc.db")
    runner = subprocess.Popen([sys.executable, str(paths.runner_script("fake"))], stdin=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    t1 = mk(store, tmp_root, "converting", pid=runner.pid); t2 = mk(store, tmp_root, "converting", pid=other.pid)
    res = recover_on_startup(store, load_config(), log=lambda s: None)
    assert res["killed"] == [runner.pid]; runner.wait(10)
    assert psutil.pid_exists(other.pid) and other.poll() is None; other.kill()
    assert store.get_task(t1)["status"] == "queued" and store.get_task(t1)["pid"] is None
    assert store.get_task(t2)["pid"] is None


def test_requeue_resets_only_converting_segments(tmp_root):
    store = Store(tmp_root / "data" / "aidoc.db")
    t = mk(store, tmp_root, "converting", segs=("done", "converting"))
    res = recover_on_startup(store, load_config(), log=lambda s: None)
    segs = store.list_segments(t)
    assert [(s["status"], s["output_path"]) for s in segs] == [("done", "p"), ("queued", None)]
    assert res["requeued"] == [t] and res["segments_reset"] == 1


def test_tmp_and_trash_cleanup(tmp_root):
    store = Store(tmp_root / "data" / "aidoc.db"); out = tmp_root / "out"
    live = mk(store, tmp_root, "queued")
    for d in [out / ".tmp" / live, out / ".tmp" / "stale", out / ".trash" / "x"]:
        d.mkdir(parents=True); (d / "f").write_text("x")
    res = recover_on_startup(store, load_config(), log=lambda s: None)
    assert (out / ".tmp" / live).exists() and not (out / ".tmp" / "stale").exists()
    assert not (out / ".trash" / "x").exists()
    assert res["tmp_removed"] == [str(out / ".tmp" / "stale")]


def test_orphaned_documents_marked(tmp_root):
    store = Store(tmp_root / "data" / "aidoc.db"); out = tmp_root / "out"
    (out / "keep").mkdir(); (out / "keep" / "keep.json").write_text("{}")
    for name in ("keep", "gone"):
        store.upsert_document(sha256=name * 16, source_path=name, output_dir=str(out / name), engine="e", quality={},
                              pages=1, lang="cht", aidoc_version="0.1.0", status="ok", work_copy_path=None,
                              work_copy_expires_at=None)
    res = recover_on_startup(store, load_config(), log=lambda s: None)
    st = {d["source_path"]: d["status"] for d in store.list_documents()}
    assert st == {"keep": "ok", "gone": "orphaned"} and len(res["orphaned"]) == 1


def test_task_running_in_another_process_is_left_alone(tmp_root):
    """Two CLIs at once: the second one's recovery must not kill or requeue the first one's live task."""
    store = Store(tmp_root / "data" / "aidoc.db"); out = tmp_root / "out"
    runner = subprocess.Popen([sys.executable, str(paths.runner_script("fake"))], stdin=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    t = mk(store, tmp_root, "converting", pid=runner.pid, segs=("done", "converting"))
    (out / ".tmp" / t).mkdir(parents=True)
    held = TaskLock(lock_path(tmp_root / "data", t)); assert held.acquire()
    try:
        res = recover_on_startup(store, load_config(), log=lambda s: None)
    finally:
        held.release()
    try:
        assert res["killed"] == [] and runner.poll() is None and res["requeued"] == []
        assert store.get_task(t)["status"] == "converting" and store.get_task(t)["pid"] == runner.pid
        assert store.list_segments(t)[1]["status"] == "converting" and (out / ".tmp" / t).exists()
    finally:
        runner.kill(); runner.wait(10)


def test_expired_work_copies_purged(tmp_root):
    import time
    store = Store(tmp_root / "data" / "aidoc.db"); w = tmp_root / "data" / "work" / "old"; w.mkdir(parents=True)
    store.upsert_document(sha256="c" * 64, source_path="c", output_dir=str(tmp_root / "out" / "c"), engine="e",
                          quality={}, pages=1, lang="cht", aidoc_version="0.1.0", status="ok",
                          work_copy_path=str(w), work_copy_expires_at=time.time() - 5)
    assert recover_on_startup(store, load_config(), log=lambda s: None)["work_purged"] == 1 and not w.exists()


def test_cli_runs_recovery_first(tmp_root, fixtures, monkeypatch, capsys):
    import shutil

    from aidoc.cli import main
    from tests.fakes.scenario import fake_env, write_scenario
    fake_env(monkeypatch, write_scenario(tmp_root / "sc.json"))
    out = tmp_root / "out"; (out / ".trash" / "old").mkdir(parents=True)
    src = tmp_root / "a.pdf"; shutil.copy(fixtures / "text.pdf", src)
    assert main(["convert", str(src), "-o", str(out)]) == 0
    assert not (out / ".trash").exists() and "trash" in capsys.readouterr().err
    (out / ".trash" / "old2").mkdir(parents=True)
    d = tmp_root / "in"; d.mkdir(); shutil.copy(fixtures / "text.pdf", d / "b.pdf")
    assert main(["batch", str(d), "-o", str(out)]) == 0 and not (out / ".trash").exists()
