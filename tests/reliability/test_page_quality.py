"""Per-page repair under failure and cancel (spec 2026-10-01 §8.2 step 8): repair never fails the task."""
import threading

import pymupdf

from aidoc.config import load_config
from aidoc.engines.registry import get_engines
from aidoc.models import ConvertOptions, TaskStatus
from aidoc.names import file_sha256
from aidoc.pipeline import run_task
from aidoc.store import Store
from tests.fakes.scenario import fake_env, write_scenario


def _ten_pages(fixtures, dst):
    d = pymupdf.open(fixtures / "broken_tounicode.pdf")
    t = pymupdf.open(fixtures / "text.pdf")
    for _ in range(8):
        d.insert_pdf(t, from_page=0, to_page=0)
    d.save(dst)
    return dst


def _run(tmp_root, monkeypatch, fixtures, **scenario):
    fake_env(monkeypatch, write_scenario(tmp_root / "sc.json", **scenario))
    store = Store(tmp_root / "data" / "aidoc.db")
    src = _ten_pages(fixtures, tmp_root / "b10.pdf")
    opts = ConvertOptions(output_dir=tmp_root / "out")
    job = store.create_job(opts, "cli")
    tid, _ = store.create_task(job, str(src), file_sha256(src), src.stat().st_size, src.stat().st_mtime, "cht",
                               str(tmp_root / "out" / "b10"))
    st = run_task(store, tid, get_engines(load_config()), load_config())
    return store, tid, st


def test_repair_engine_crash_degrades_to_warn(tmp_root, monkeypatch, fixtures):
    store, tid, st = _run(tmp_root, monkeypatch, fixtures, garbled_pages=[1], default="garbled",
                          rules=[{"match": {"source_glob": "pages.pdf"}, "behavior": "crash"}])
    assert st == TaskStatus.done and store.list_documents()[0]["status"] == "warn"
    assert store.get_task(tid)["quality"]["pages_unrepaired"] == 1


def test_repair_engine_error_degrades_to_warn(tmp_root, monkeypatch, fixtures):
    store, _, st = _run(tmp_root, monkeypatch, fixtures, garbled_pages=[1], default="garbled",
                        rules=[{"match": {"source_glob": "pages.pdf"}, "behavior": "error"}])
    assert st == TaskStatus.done and store.list_documents()[0]["status"] == "warn"


def test_cancel_during_repair_cancels_the_task(tmp_root, monkeypatch, fixtures):
    from aidoc.pipeline import run_task as rt
    fake_env(monkeypatch, write_scenario(tmp_root / "sc.json", garbled_pages=[1], default="garbled", slow_s=30,
                                         rules=[{"match": {"source_glob": "pages.pdf"}, "behavior": "slow_ok"}]))
    store = Store(tmp_root / "data" / "aidoc.db")
    src = _ten_pages(fixtures, tmp_root / "b10.pdf")
    job = store.create_job(ConvertOptions(output_dir=tmp_root / "out"), "cli")
    tid, _ = store.create_task(job, str(src), file_sha256(src), src.stat().st_size, src.stat().st_mtime, "cht",
                               str(tmp_root / "out" / "b10"))
    cancel = threading.Event()
    threading.Timer(5.0, cancel.set).start()
    assert rt(store, tid, get_engines(load_config()), load_config(), cancel=cancel) == TaskStatus.cancelled
