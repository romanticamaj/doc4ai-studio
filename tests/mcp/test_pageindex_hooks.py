import time

from aidoc.batch import register_source
from aidoc.config import load_config
from aidoc.engines.registry import get_engines
from aidoc.models import ConvertOptions
from aidoc.pipeline import run_task
from aidoc.store import Store
from tests.fakes.scenario import fake_env, write_scenario


def test_conversion_writes_page_index(tmp_root, fixtures, monkeypatch):
    fake_env(monkeypatch, write_scenario(tmp_root / "sc.json", pages_per_doc=3))
    cfg = load_config()
    store = Store(tmp_root / "data" / "aidoc.db")
    opts = ConvertOptions(output_dir=tmp_root / "out")
    job = store.create_job(opts, "cli")
    tid, _ = register_source(store, job, fixtures / "text.pdf", opts)
    assert run_task(store, tid, get_engines(cfg), cfg).value in ("done", "low")
    doc = next(iter(store.list_documents()))
    rows = store._qa("SELECT page FROM page_index WHERE doc_id=? ORDER BY page", (doc["id"],))
    assert [r["page"] for r in rows] == [1, 2, 3]
    store.close()


def test_server_start_backfills_missing_index(tmp_root, monkeypatch):
    from aidoc.server.app import build_context
    fake_env(monkeypatch, write_scenario(tmp_root / "sc.json"))
    store = Store(tmp_root / "data" / "aidoc.db")
    out = tmp_root / "out" / "old"
    out.mkdir(parents=True)
    (out / "old.md").write_text("<!-- page: 1 -->\nold text\n<!-- page: 2 -->\nmore\n", encoding="utf-8")
    (out / "old.json").write_text("{}", encoding="utf-8")
    store.upsert_document(sha256="s", source_path="old.pdf", output_dir=str(out), engine="mineru",
                          quality={"score": 1, "level": "ok", "reasons": []}, pages=2, lang="cht", aidoc_version="0.1.0",
                          status="ok")
    store.close()
    ctx = build_context(load_config(), token=None, start_workers=True)
    try:
        deadline = time.time() + 5
        while time.time() < deadline and not ctx.store.page_index_doc_ids():
            time.sleep(0.05)
        assert len(ctx.store.page_index_doc_ids()) == 1
    finally:
        ctx.close()
