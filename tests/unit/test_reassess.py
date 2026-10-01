import json
import os
import shutil
import time

from aidoc.reassess import doc_flags, reassess_all
from aidoc.store import Store


def _legacy_doc(store, tmp_root, fixtures, markers):
    import pymupdf
    out = tmp_root / "out" / "big"
    out.mkdir(parents=True)
    src = pymupdf.open(fixtures / "big.pdf")
    body = "".join((f"<!-- page: {n} -->\n" if n in markers else "") + src[n - 1].get_text() + "\n\n"
                   for n in range(1, 46))
    (out / "big.md").write_text(body, encoding="utf-8")
    (out / "big.json").write_text(json.dumps({"sha256": "x"}), encoding="utf-8")
    work = tmp_root / "data" / "work" / "t1"
    work.mkdir(parents=True)
    shutil.copy(fixtures / "big.pdf", work / "src.pdf")
    return store.upsert_document(sha256="x", source_path="big.pdf", output_dir=str(out), engine="mineru",
                                 quality={"score": 1.0, "level": "ok", "reasons": [], "metrics": {}}, pages=45,
                                 lang="cht", aidoc_version="0.1.0", status="ok", work_copy_path=str(work),
                                 created_at=time.time())


def test_legacy_mineru_doc_with_segment_markers_becomes_low(tmp_root, fixtures):
    store = Store(tmp_root / "data" / "aidoc.db")
    did = _legacy_doc(store, tmp_root, fixtures, markers={1, 41})
    md_path = tmp_root / "out" / "big" / "big.md"
    before = os.stat(md_path).st_mtime_ns
    counts = reassess_all(store, log=lambda s: None)
    doc = store.get_document(did)
    assert counts["assessed"] == 1 and counts["page_map_incomplete"] == 1
    assert doc["status"] == "low" and doc["quality"]["page_map"]["found"] == 2
    assert doc["quality"]["page_map"]["method"] == "legacy" and "page_map_incomplete" in doc_flags(doc)
    assert os.stat(md_path).st_mtime_ns == before                       # outputs are never rewritten
    assert reassess_all(store, log=lambda s: None)["assessed"] == 0     # page_check recorded: not redone
    assert reassess_all(store, force=True, log=lambda s: None)["assessed"] == 1


def test_complete_legacy_doc_stays_ok(tmp_root, fixtures):
    store = Store(tmp_root / "data" / "aidoc.db")
    did = _legacy_doc(store, tmp_root, fixtures, markers=set(range(1, 46)))
    reassess_all(store, log=lambda s: None)
    assert store.get_document(did)["status"] == "ok" and doc_flags(store.get_document(did)) == []


def test_unassessed_flag_and_store_flag_filter(tmp_root, fixtures):
    store = Store(tmp_root / "data" / "aidoc.db")
    did = _legacy_doc(store, tmp_root, fixtures, markers={1, 41})
    assert doc_flags(store.get_document(did)) == ["unassessed"]
    assert [d["id"] for d in store.list_documents(flag="unassessed")] == [did]
    reassess_all(store, log=lambda s: None)
    assert [d["id"] for d in store.list_documents(flag="page_map_incomplete")] == [did]
    assert store.list_documents(flag="unassessed") == []
    # pages without a marker are unrepaired flagged pages (spec §5.2), so page_quality is set as well
    assert [d["id"] for d in store.list_documents(flag="page_quality")] == [did]
