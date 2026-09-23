import shutil
import sys
import threading

import pytest

from aidoc.cli import main
from tests.fakes.scenario import fake_env, write_scenario

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows share-mode semantics")


def test_force_with_open_handle_succeeds_after_backoff(tmp_root, fixtures, monkeypatch):
    fake_env(monkeypatch, write_scenario(tmp_root / "sc.json"))
    src = tmp_root / "a.pdf"; shutil.copy(fixtures / "text.pdf", src); out = tmp_root / "out"
    assert main(["convert", str(src), "-o", str(out)]) == 0
    import aidoc.fsops
    real_sleep, slept = aidoc.fsops.time.sleep, []

    def sleep(sec):                       # record the backoff waits, still really wait
        slept.append(sec)
        real_sleep(sec)
    monkeypatch.setattr("aidoc.fsops.time.sleep", sleep)
    fh = open(out / "a" / "a.md")  # noqa: SIM115  closed by the timer
    threading.Timer(0.6, fh.close).start()
    assert main(["convert", str(src), "-o", str(out), "--force"]) == 0
    assert slept and slept[0] == 0.2          # the replace hit the open handle and backed off (0.2 s x 5)
    assert not (out / ".trash").exists() or not any((out / ".trash").iterdir())
    assert (out / "a" / "a.md").exists()


def test_force_with_long_held_handle_is_transient(tmp_root, fixtures, monkeypatch):
    fake_env(monkeypatch, write_scenario(tmp_root / "sc.json"))
    monkeypatch.setattr("aidoc.fsops.time.sleep", lambda s: None)
    src = tmp_root / "a.pdf"; shutil.copy(fixtures / "text.pdf", src); out = tmp_root / "out"
    assert main(["convert", str(src), "-o", str(out)]) == 0
    with open(out / "a" / "a.md"):
        assert main(["convert", str(src), "-o", str(out), "--force", "--json"]) == 1
    from aidoc.store import Store
    t = Store(tmp_root / "data" / "aidoc.db").list_tasks()[0]
    assert t["error_kind"] == "transient" and (out / "a" / "a.md").exists()
    assert not (out / ".trash").exists() or not any((out / ".trash").iterdir())
