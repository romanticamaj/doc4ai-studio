import json
import sys
import threading
import time
import pytest
from aidoc import fsops


def test_atomic_write_text_replaces_and_leaves_no_tmp(tmp_path):
    p = tmp_path / "a.md"
    p.write_text("old", encoding="utf-8")
    fsops.atomic_write_text(p, "new 中文")
    assert p.read_text(encoding="utf-8") == "new 中文"
    assert list(tmp_path.iterdir()) == [p]


def test_atomic_json_and_lines(tmp_path):
    fsops.atomic_write_json(tmp_path / "x.json", {"a": "中"})
    assert json.loads((tmp_path / "x.json").read_text(encoding="utf-8")) == {"a": "中"}
    fsops.atomic_write_lines(tmp_path / "m.jsonl", ['{"a":1}', '{"b":2}'])
    assert (tmp_path / "m.jsonl").read_text(encoding="utf-8") == '{"a":1}\n{"b":2}\n'


def test_retry_fs_eventually_succeeds(monkeypatch):
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError("busy")
        return "ok"
    monkeypatch.setattr(fsops.time, "sleep", lambda s: None)
    assert fsops.retry_fs(flaky) == "ok" and calls["n"] == 3


def test_retry_fs_gives_up(monkeypatch):
    monkeypatch.setattr(fsops.time, "sleep", lambda s: None)
    with pytest.raises(fsops.FsBusyError):
        fsops.retry_fs(lambda: (_ for _ in ()).throw(PermissionError("x")))


def test_three_step_fresh_target(tmp_path):
    tmp = tmp_path / ".tmp" / "t1"
    tmp.mkdir(parents=True)
    (tmp / "f").write_text("1")
    final = tmp_path / "doc"
    trash = tmp_path / ".trash" / "t1"
    fsops.replace_dir_three_step(tmp, final, trash)
    assert (final / "f").read_text() == "1" and not tmp.exists() and not trash.exists()


def test_three_step_existing_target_replaced(tmp_path):
    final = tmp_path / "doc"
    final.mkdir()
    (final / "old").write_text("o")
    tmp = tmp_path / ".tmp" / "t2"
    tmp.mkdir(parents=True)
    (tmp / "new").write_text("n")
    fsops.replace_dir_three_step(tmp, final, tmp_path / ".trash" / "t2")
    assert (final / "new").exists() and not (final / "old").exists()
    assert not (tmp_path / ".trash" / "t2").exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows share-mode semantics")
def test_three_step_waits_for_open_handle(tmp_path, monkeypatch):
    real_sleep = time.sleep  # fsops.time IS the time module; capture before patching
    monkeypatch.setattr(fsops.time, "sleep", lambda s: real_sleep(min(s, 0.05)))
    final = tmp_path / "doc"
    final.mkdir()
    (final / "held.md").write_text("o")
    tmp = tmp_path / ".tmp" / "t3"
    tmp.mkdir(parents=True)
    (tmp / "new").write_text("n")
    fh = open(final / "held.md", "r")
    threading.Timer(0.12, fh.close).start()
    fsops.replace_dir_three_step(tmp, final, tmp_path / ".trash" / "t3")
    assert (final / "new").exists() and not (tmp_path / ".trash").exists() or not any((tmp_path / ".trash").iterdir())


@pytest.mark.skipif(sys.platform != "win32", reason="Windows share-mode semantics")
def test_three_step_busy_raises_and_leaves_final_untouched(tmp_path, monkeypatch):
    monkeypatch.setattr(fsops.time, "sleep", lambda s: None)
    final = tmp_path / "doc"
    final.mkdir()
    (final / "held.md").write_text("o")
    tmp = tmp_path / ".tmp" / "t4"
    tmp.mkdir(parents=True)
    (tmp / "new").write_text("n")
    with open(final / "held.md", "r"):
        with pytest.raises(fsops.FsBusyError):
            fsops.replace_dir_three_step(tmp, final, tmp_path / ".trash" / "t4")
    assert (final / "held.md").exists() and not (tmp_path / ".trash" / "t4").exists() and tmp.exists()
