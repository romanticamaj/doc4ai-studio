import shutil

from aidoc import fsops
from aidoc.config import load_config
from aidoc.engines.registry import get_engines
from aidoc.models import ConvertOptions, TaskStatus
from aidoc.names import file_sha256
from aidoc.pipeline import run_task
from aidoc.recovery import recover_on_startup
from aidoc.store import Store
from tests.fakes.scenario import fake_env, write_scenario


def _task(tmp_root, fixtures, store):
    src = tmp_root / "a.pdf"; shutil.copy(fixtures / "text.pdf", src)
    job = store.create_job(ConvertOptions(output_dir=tmp_root / "out"), "cli")
    return store.create_task(job, str(src), file_sha256(src), 1, 1.0, "cht", str(tmp_root / "out" / "a"))[0]


def test_failure_during_write_leaves_no_final_dir(tmp_root, fixtures, monkeypatch):
    fake_env(monkeypatch, write_scenario(tmp_root / "sc.json")); cfg = load_config()
    store = Store(tmp_root / "data" / "aidoc.db")
    tid = _task(tmp_root, fixtures, store)
    orig = fsops.atomic_write_json

    def boom(path, obj):
        if path.name.endswith(".json") and ".tmp" in str(path):
            raise OSError(28, "No space left on device")
        return orig(path, obj)
    monkeypatch.setattr("aidoc.output.fsops.atomic_write_json", boom)
    assert run_task(store, tid, get_engines(cfg), cfg) == TaskStatus.failed
    assert not (tmp_root / "out" / "a").exists()
    assert store.get_task(tid)["error_kind"] in ("transient", "input")
    recover_on_startup(store, cfg, log=lambda s: None)
    assert not (tmp_root / "out" / ".tmp" / tid).exists()


def test_finalize_busy_is_transient(tmp_root, fixtures, monkeypatch):
    fake_env(monkeypatch, write_scenario(tmp_root / "sc.json")); cfg = load_config()
    store = Store(tmp_root / "data" / "aidoc.db")
    tid = _task(tmp_root, fixtures, store)
    monkeypatch.setattr("aidoc.output.fsops.replace_dir_three_step",
                        lambda *a, **k: (_ for _ in ()).throw(fsops.FsBusyError(16, "busy")))
    assert run_task(store, tid, get_engines(cfg), cfg) == TaskStatus.failed
    assert store.get_task(tid)["error_kind"] == "transient" and not (tmp_root / "out" / "a").exists()
    assert not (tmp_root / "out" / ".tmp" / tid).exists()
