import json
import os
import subprocess
import sys

from aidoc import lockfile as lf


def test_acquire_and_release(tmp_path):
    p = tmp_path / "aidoc.lock"
    info = {"pid": os.getpid(), "started_at": 1.0, "host": "127.0.0.1", "port": 8765, "token": None}
    assert lf.acquire_lock(p, info) and lf.read_lock(p)["port"] == 8765
    assert not lf.acquire_lock(p, dict(info, pid=999999))     # live owner: this process
    lf.release_lock(p)
    assert not p.exists()


def test_stale_lock_overwritten(tmp_path):
    p = tmp_path / "aidoc.lock"
    p.write_text(json.dumps({"pid": 999999, "started_at": 0, "host": "127.0.0.1", "port": 1, "token": None}))
    assert lf.acquire_lock(p, {"pid": os.getpid(), "started_at": 1, "host": "h", "port": 2, "token": None})
    assert lf.read_lock(p)["port"] == 2


def test_pid_reuse_by_non_aidoc_process_is_stale(tmp_path):
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"])
    p = tmp_path / "aidoc.lock"
    p.write_text(json.dumps({"pid": other.pid, "started_at": 0, "host": "h", "port": 1, "token": None}))
    try:
        assert lf.is_live(lf.read_lock(p)) is False
        assert lf.acquire_lock(p, {"pid": os.getpid(), "started_at": 1, "host": "h", "port": 3, "token": None})
    finally:
        other.kill()


def test_release_only_own(tmp_path):
    p = tmp_path / "aidoc.lock"
    p.write_text(json.dumps({"pid": 4242, "started_at": 0, "host": "h", "port": 1, "token": None}))
    lf.release_lock(p)
    assert p.exists()


def test_unparseable_lock_is_none_and_stale(tmp_path):
    p = tmp_path / "aidoc.lock"
    p.write_text("{not json")
    assert lf.read_lock(p) is None
    assert lf.acquire_lock(p, {"pid": os.getpid(), "started_at": 1, "host": "h", "port": 4, "token": None})
    assert lf.read_lock(p)["port"] == 4
