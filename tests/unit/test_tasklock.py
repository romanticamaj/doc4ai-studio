import json
import shutil
import subprocess
import sys
import time

import pytest

from aidoc.cli import main
from aidoc.models import ConvertOptions, TaskStatus
from aidoc.store import Store, TaskBusyError
from aidoc.tasklock import TaskLock, is_locked, lock_path
from tests.fakes.scenario import fake_env, write_scenario


def test_lock_is_exclusive_and_released(tmp_path):
    p = tmp_path / "locks" / "t.lock"
    a = TaskLock(p)
    assert a.acquire() and is_locked(p)
    assert not TaskLock(p).acquire()
    a.release()
    assert not is_locked(p)


def test_lock_released_when_holder_process_dies(tmp_path):
    p = tmp_path / "t.lock"
    code = (f"import time; from aidoc.tasklock import TaskLock; l = TaskLock({str(p)!r}); "
            "assert l.acquire(); print('ok', flush=True); time.sleep(60)")
    proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    assert proc.stdout.readline().strip() == "ok"
    assert is_locked(p)
    proc.kill(); proc.wait(10)
    deadline = time.time() + 5
    while is_locked(p) and time.time() < deadline:
        time.sleep(0.1)
    assert not is_locked(p)


def test_create_task_refuses_to_steal_a_live_task(tmp_root):
    """P1 verifier 4: a second convert of the same file must not re-parent a row another process is running."""
    store = Store(tmp_root / "data" / "aidoc.db")
    j = store.create_job(ConvertOptions(output_dir=tmp_root / "out"), "cli")
    tid, _ = store.create_task(j, "a.pdf", "k" * 64, 1, 1.0, "cht", "out/k")
    store.update_task(tid, status=TaskStatus.converting)
    held = TaskLock(lock_path(tmp_root / "data", tid)); assert held.acquire()
    j2 = store.create_job(ConvertOptions(output_dir=tmp_root / "out"), "cli")
    with pytest.raises(TaskBusyError) as e:
        store.create_task(j2, "a.pdf", "k" * 64, 1, 1.0, "cht", "out/k")
    assert e.value.task_id == tid and store.get_task(tid)["job_id"] == j
    held.release()                                         # holder gone -> stale row is resumable again
    assert store.create_task(j2, "a.pdf", "k" * 64, 1, 1.0, "cht", "out/k") == (tid, True)


def test_second_convert_of_a_running_file_fails_cleanly(tmp_root, fixtures, monkeypatch, capsys):
    fake_env(monkeypatch, write_scenario(tmp_root / "sc.json"))
    src = tmp_root / "a.pdf"; shutil.copy(fixtures / "text.pdf", src)
    from aidoc.batch import register_source
    store = Store(tmp_root / "data" / "aidoc.db")
    opts = ConvertOptions(output_dir=(tmp_root / "out").resolve())
    tid, _ = register_source(store, store.create_job(opts, "cli"), src.resolve(), opts)
    store.update_task(tid, status=TaskStatus.converting)
    held = TaskLock(lock_path(tmp_root / "data", tid)); assert held.acquire()
    try:
        assert main(["convert", str(src), "-o", str(tmp_root / "out"), "--json"]) == 1
    finally:
        held.release()
    out = json.loads(capsys.readouterr().out)
    assert out["error_kind"] == "input" and out["error_msg"].startswith("already_converting")
    assert store.get_task(tid)["status"] == "converting"


def test_run_task_does_not_touch_a_task_locked_elsewhere(tmp_root, fixtures, monkeypatch):
    from aidoc.config import load_config
    from aidoc.engines.registry import get_engines
    from aidoc.pipeline import run_task
    fake_env(monkeypatch, write_scenario(tmp_root / "sc.json"))
    store = Store(tmp_root / "data" / "aidoc.db")
    j = store.create_job(ConvertOptions(output_dir=tmp_root / "out"), "cli")
    tid, _ = store.create_task(j, str(tmp_root / "a.pdf"), "k" * 64, 1, 1.0, "cht", str(tmp_root / "out" / "a"))
    store.update_task(tid, status=TaskStatus.converting)
    held = TaskLock(lock_path(tmp_root / "data", tid)); assert held.acquire()
    try:
        assert run_task(store, tid, get_engines(load_config()), load_config()) == TaskStatus.converting
    finally:
        held.release()
    assert store.get_task(tid)["status"] == "converting"
