import json
import os
import shutil
import time

from aidoc.quality import PAGE_CHECK_VERSION
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


def test_reassess_never_overwrites_a_newer_conversion(tmp_root, fixtures, monkeypatch):
    """A conversion that finishes while the (slow) assessment runs keeps its own quality (review finding)."""
    import aidoc.reassess as ra
    store = Store(tmp_root / "data" / "aidoc.db")
    did = _legacy_doc(store, tmp_root, fixtures, markers={1, 41})
    fresh = {"score": 1.0, "level": "ok", "reasons": [], "metrics": {}, "page_check": 1,
             "page_map": {"expected": 45, "found": 45, "coverage": 1.0, "missing": [], "method": "mineru_render_plan"},
             "pages": [], "pages_flagged": 0, "pages_unrepaired": 0}
    real = ra.assess

    def assess_while_converting(*a, **k):
        store.update_document_quality(did, fresh, "ok")             # the reconversion lands meanwhile
        return real(*a, **k)
    monkeypatch.setattr(ra, "assess", assess_while_converting)
    assert reassess_all(store, log=lambda s: None)["assessed"] == 0
    assert store.get_document(did)["quality"]["page_map"]["method"] == "mineru_render_plan"


def test_invalid_page_map_is_flagged_like_an_incomplete_one():
    doc = {"source_path": "a.pdf", "quality": {"reasons": ["page_map_invalid"], "page_check": PAGE_CHECK_VERSION,
                                               "pages_unrepaired": 0}}
    assert doc_flags(doc) == ["page_map_incomplete"]


# ---- verifier round: no PDF -> not stamped; per-page history carried forward (also from the sidecar)

def _paged_doc(store, tmp_root, fixtures, *, quality, sidecar_quality=None, work=True, secs_override=None):
    import pymupdf
    out = tmp_root / "out" / "pf"
    out.mkdir(parents=True)
    with pymupdf.open(fixtures / "paged_furniture.pdf") as d:
        secs = {i: p.get_text() for i, p in enumerate(d, 1)}
    secs.update(secs_override or {})
    (out / "pf.md").write_text("".join(f"<!-- page: {i} -->\n\n{secs[i]}\n\n" for i in sorted(secs)), encoding="utf-8")
    (out / "pf.json").write_text(json.dumps({"sha256": "y", "quality": sidecar_quality or quality}), encoding="utf-8")
    wc = None
    if work:
        wc = tmp_root / "data" / "work" / "t2"
        wc.mkdir(parents=True)
        shutil.copy(fixtures / "paged_furniture.pdf", wc / "src.pdf")
    return store.upsert_document(sha256="y", source_path="pf.pdf", output_dir=str(out), engine="docling",
                                 quality=quality, pages=8, lang="cht", aidoc_version="0.1.0", status=quality["level"],
                                 work_copy_path=str(wc) if wc else None, created_at=time.time())


def test_a_document_without_its_pdf_is_not_stamped_as_checked(tmp_root, fixtures):
    store = Store(tmp_root / "data" / "aidoc.db")
    q = {"score": 1.0, "level": "ok", "reasons": [], "metrics": {}, "page_check": 1, "pages": []}
    did = _paged_doc(store, tmp_root, fixtures, quality=q, work=False)
    assert reassess_all(store, log=lambda s: None)["assessed"] == 0
    doc = store.get_document(did)
    assert doc["quality"] == q and "unassessed" in doc_flags(doc)           # visible, not silently "ok"


def test_reassess_keeps_repaired_pages_failures_and_method(tmp_root, fixtures):
    store = Store(tmp_root / "data" / "aidoc.db")
    q = {"score": 1.0, "level": "ok", "reasons": [], "metrics": {}, "page_check": 1,
         "page_map": {"expected": 8, "found": 8, "coverage": 1.0, "method": "docling_per_page"},
         "pages": [{"page": 2, "reasons": ["broken_text_layer"], "repaired_by": "docling:pypdfium_full_page_ocr"},
                   {"page": 5, "reasons": ["page_conversion_failed"], "error": "Page failed to parse."}],
         "pages_flagged": 2, "pages_unrepaired": 1}
    did = _paged_doc(store, tmp_root, fixtures, quality=q, secs_override={5: ""})
    reassess_all(store, log=lambda s: None)
    doc = store.get_document(did)
    got = {p["page"]: p for p in doc["quality"]["pages"]}
    assert got[2]["repaired_by"] == "docling:pypdfium_full_page_ocr"
    assert "page_conversion_failed" in got[5]["reasons"] and doc["status"] == "warn"
    assert doc["quality"]["page_map"]["method"] == "docling_per_page"
    assert doc["quality"]["page_check"] == PAGE_CHECK_VERSION


def test_history_wiped_by_an_earlier_regrade_is_recovered_from_the_sidecar(tmp_root, fixtures):
    store = Store(tmp_root / "data" / "aidoc.db")
    wiped = {"score": 1.0, "level": "ok", "reasons": [], "metrics": {}, "page_check": 2, "pages": [],
             "page_map": {"expected": 8, "found": 8, "coverage": 1.0, "method": "legacy"}}
    original = {"score": 0.99, "level": "ok", "reasons": [], "metrics": {}, "page_check": 1,
                "page_map": {"expected": 8, "found": 8, "coverage": 1.0, "method": "docling_per_page"},
                "pages": [{"page": 2, "reasons": ["broken_text_layer"],
                           "repaired_by": "docling:pypdfium_full_page_ocr"}]}
    did = _paged_doc(store, tmp_root, fixtures, quality=wiped, sidecar_quality=original)
    reassess_all(store, log=lambda s: None)
    q = store.get_document(did)["quality"]
    assert [p.get("repaired_by") for p in q["pages"] if p["page"] == 2] == ["docling:pypdfium_full_page_ocr"]
    assert q["page_map"]["method"] == "docling_per_page" and q["page_check"] == PAGE_CHECK_VERSION


def test_page_check_version_is_3():
    assert PAGE_CHECK_VERSION == 3
