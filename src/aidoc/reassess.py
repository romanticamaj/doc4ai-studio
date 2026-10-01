"""Re-assess existing outputs for page map and page quality (spec 2026-10-01 §11).

Runs at server start (background) and on POST /documents/rescan. Only `documents.quality_json` and
`documents.status` change: output files and sidecars are produced by conversions alone and are never rewritten.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from aidoc.models import ProbeResult
from aidoc.names import file_sha256
from aidoc.quality import PAGE_CHECK_VERSION, assess

FLAGS = ("page_map_incomplete", "page_quality", "unassessed")
_ASSESSABLE = ("ok", "warn", "low")


def _is_pdf_doc(doc: dict) -> bool:
    q = doc.get("quality") or {}
    return Path(str(doc.get("source_path") or "")).suffix.lower() == ".pdf" or bool(q.get("page_map"))


def doc_flags(doc: dict) -> list[str]:
    """Spec §9.2 flags, derived from the stored quality (the web never recomputes the rules)."""
    q = doc.get("quality") or {}
    reasons = set(q.get("reasons") or [])
    out = []
    if reasons & {"page_map_incomplete", "page_map_misaligned"}:
        out.append("page_map_incomplete")
    if (q.get("pages_unrepaired") or 0) > 0 or "pages_flagged" in reasons:
        out.append("page_quality")
    if _is_pdf_doc(doc) and (q.get("page_check") or 0) < PAGE_CHECK_VERSION:
        out.append("unassessed")
    return out


def _source_pdf(doc: dict) -> Path | None:
    wc = doc.get("work_copy_path")
    if wc and Path(wc).is_dir():
        for f in sorted(Path(wc).glob("src.*")):
            if f.is_file() and f.suffix.lower() == ".pdf":
                return f
    src = Path(str(doc.get("source_path") or ""))
    if src.is_absolute() and src.is_file() and src.suffix.lower() == ".pdf":
        try:
            if file_sha256(src) == doc.get("sha256"):
                return src
        except OSError:
            return None
    return None


def _markdown_path(doc: dict) -> Path:
    out = Path(doc["output_dir"])
    return out / f"{out.name}.md"


def reassess_document(store, doc: dict, *, force: bool = False) -> dict | None:
    """New quality dict for one existing document, written to the store; None when skipped."""
    if doc.get("status") not in _ASSESSABLE or not doc.get("pages"):
        return None
    if Path(str(doc.get("source_path") or "")).suffix.lower() != ".pdf":
        return None
    old = doc.get("quality") or {}
    if not force and (old.get("page_check") or 0) >= PAGE_CHECK_VERSION:
        return None
    md_path = _markdown_path(doc)
    if not md_path.is_file():
        return None
    md = md_path.read_text(encoding="utf-8")
    pdf = _source_pdf(doc)
    if pdf is not None:
        from aidoc.probe import probe_file
        probe = probe_file(pdf)
        if probe.error or probe.pages != doc["pages"]:
            probe, pdf = ProbeResult(kind="pdf", ext=".pdf", size=0, pages=doc["pages"]), None
    else:
        probe = ProbeResult(kind="pdf", ext=".pdf", size=0, pages=doc["pages"])
    q = assess(md, probe, doc["pages"], pdf=pdf, page_map_method="legacy")
    if q.page_map is not None and pdf is None:
        q.page_map["alignment"] = None
    q.metrics = {**(old.get("metrics") or {}), **q.metrics}
    out = q.to_json()
    store.update_document_quality(doc["id"], out, q.level)
    return out


def reassess_all(store, *, force: bool = False, log: Callable[[str], None] = print) -> dict:
    """Assess every document that needs it; the flag counts cover the whole library afterwards."""
    assessed = 0
    for doc in store.list_documents():
        try:
            q = reassess_document(store, doc, force=force)
        except Exception as e:  # noqa: BLE001  one unreadable document must not stop the others
            log(f"reassess {doc['id']}: failed: {type(e).__name__}: {e}")
            continue
        if q is None:
            continue
        assessed += 1
        pm = q.get("page_map") or {}
        log(f"reassess {doc['id']} ({Path(doc['output_dir']).name}): {q['level']} reasons={q['reasons']} "
            f"page_map={pm.get('found')}/{pm.get('expected')} unrepaired={q.get('pages_unrepaired')}")
    docs = [d for d in store.list_documents() if d.get("status") != "orphaned"]
    return {"assessed": assessed,
            "page_map_incomplete": sum("page_map_incomplete" in doc_flags(d) for d in docs),
            "page_quality": sum("page_quality" in doc_flags(d) for d in docs)}
