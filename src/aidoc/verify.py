"""`aidoc verify` — score ANY Markdown against its source PDF with the per-page quality model
(spec 2026-10-05 verify §2; index A24, A26).

`verify_markdown()` is pure: no store, no file writes, no network; the CLI, API and MCP tools are thin shells.
"""
from __future__ import annotations

import hashlib
import re
import time
from datetime import datetime
from pathlib import Path

from aidoc import __version__, markers, pagemap, quality
from aidoc.models import (
    EXIT_FAILED,
    EXIT_OK,
    EXIT_UNVERIFIABLE,
    EXIT_USAGE,
    ProbeResult,
    VerifyReport,
)

__all__ = [
    "EXIT_FAILED",
    "EXIT_OK",
    "EXIT_UNVERIFIABLE",
    "EXIT_USAGE",
    "NeedsPagesError",
    "exit_code",
    "summary_text",
    "verify_markdown",
]

VERIFY_VERSION = 1
DROPPED_LAYER_MIN = 120        # squashed text-layer chars a page needs before an empty section means "dropped"
DROPPED_SECTION_MAX = 20       # non-space chars (markup stripped) below which a section counts as empty
INFER_MIN_CONFIDENCE = 0.5     # inferred page map below this -> verifiable = false
TEXT_LAYER_MIN = 20            # squashed chars for a page to count as having a text layer
IMAGE_MISSING = "<!-- image missing -->"
_DOLLAR = re.compile(r"\$")


class NeedsPagesError(ValueError):
    """Neither a PDF nor a page count: nothing page-level can be said (CLI exit 3, spec §2.5)."""


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _pdf_pass(pdf: Path) -> tuple[list[str], list[dict]]:
    """One pass over the PDF: squashed text layers (as `pagemap.pdf_text_layers`) and `probe.page_features`."""
    import pymupdf

    from aidoc.probe import page_features
    layers, feats = [], []
    with pymupdf.open(pdf) as doc:
        for i in range(doc.page_count):
            text = doc[i].get_text()
            layers.append(pagemap.squash(text))
            feats.append(page_features(doc, i, text))
    return layers, feats


def _section_chars(section: str) -> int:
    return sum(1 for c in quality.strip_markup(section) if not c.isspace())


def _merge(entries: dict[int, dict], page: int, *, reason: str | None = None, advisory: str | None = None,
           metrics: dict | None = None) -> None:
    e = entries.setdefault(page, {"page": page, "reasons": []})
    if reason and reason not in e["reasons"]:
        e["reasons"].append(reason)
    if advisory:
        e["advisories"] = sorted(set(e.get("advisories") or []) | {advisory})
    if metrics:
        e["metrics"] = {**(e.get("metrics") or {}), **metrics}


def verify_markdown(md: str, pdf: Path | None, *, pages: int | None = None, fmt: str = "auto",
                    marker_regex: str | None = None, sample: int = 30, previous: dict | None = None,
                    input_name: str = "<markdown>") -> VerifyReport:
    """Spec §2.2–§2.4. Raises ValueError for unusable input (bad regex/format, unreadable PDF) and
    NeedsPagesError when there is neither a PDF nor `pages`."""
    t0 = time.perf_counter()
    timing: dict[str, float] = {}
    raw = md
    md = md.removeprefix("﻿")
    newline = "crlf" if "\r\n" in md else "lf"
    md = md.replace("\r\n", "\n")
    if pdf is None and pages is None:
        raise NeedsPagesError("no PDF and no page count: pass --pdf or --pages")

    # ---- PDF facts
    probe: ProbeResult
    layers: list[str] | None = None
    pdf_info: dict | None = None
    if pdf is not None:
        pdf = Path(pdf)
        if not pdf.is_file():
            raise ValueError(f"pdf_unreadable: not found: {pdf}")
        from aidoc.probe import probe_file
        t = time.perf_counter()
        probe = probe_file(pdf)
        timing["probe"] = round(time.perf_counter() - t, 3)
        if probe.error or not probe.pages:
            raise ValueError(f"pdf_unreadable: {probe.error or 'no pages'}")
        t = time.perf_counter()
        layers, feats = _pdf_pass(pdf)
        timing["layers"] = round(time.perf_counter() - t, 3)
        n = probe.pages
        pdf_info = {"path": str(pdf), "sha256": _sha256_file(pdf), "pages": n,
                    "text_layer_pages": sum(1 for x in layers if len(x) >= TEXT_LAYER_MIN),
                    "broken_fonts": list(probe.broken_fonts),
                    "broken_font_pages_count": len(probe.broken_font_pages),
                    "blank_pages": list(probe.blank_pages)}
    else:
        n = int(pages)
        if n < 1:
            raise ValueError("pages must be >= 1")
        probe = ProbeResult(kind="pdf", ext=".pdf", size=0, pages=n)

    # ---- page markers: dialect -> `<!-- page: N -->`
    forced = fmt != "auto" or bool(marker_regex)
    if fmt == "auto" and marker_regex:
        fmt_eff = "custom"
    elif fmt == "auto":
        fmt_eff = markers.detect(md)
    else:
        fmt_eff = fmt
    md_marked, minfo = markers.to_aidoc(md, fmt_eff, expected_pages=n, marker_regex=marker_regex)
    input_info = {"markdown": input_name, "sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
                  "bytes": len(raw.encode("utf-8")), "newline": newline, "format_detected": fmt_eff,
                  "markers_found": minfo["markers_found"], "markers_applied": minfo["applied"]}
    if minfo["reason"]:
        input_info["markers_reason"] = minfo["reason"]
    if forced:
        input_info["format_forced"] = True

    has_layers = layers is not None and any(len(x) >= TEXT_LAYER_MIN for x in layers)
    verifiable: bool | str = True if pdf is not None else "partial"
    inferred: dict | None = None
    t = time.perf_counter()
    if not minfo["applied"]:
        if pdf is not None and has_layers:
            inferred = pagemap.infer_pages(md, layers, exclude=probe.broken_font_pages)
            timing["infer"] = round(time.perf_counter() - t, 3)
            if inferred["anchored"] and inferred["confidence"] >= INFER_MIN_CONFIDENCE:
                md_marked = pagemap.apply_inferred(md, inferred["sections"])
            else:
                verifiable = False
        elif pdf is not None:
            verifiable = False                  # scanned PDF, no markers: nothing to anchor on (spec §2.2)

    # ---- quality: the same model as our own conversions (no repair info: every flagged page is unrepaired)
    t = time.perf_counter()
    page_level = minfo["applied"] or (inferred is not None and verifiable is True)
    if page_level:
        q = quality.assess(md_marked, probe, n, pdf=pdf, page_map_method=fmt_eff if inferred is None else "inferred",
                           layers=layers, last_resort=True, align_sample=sample if sample > 0 else n,
                           skip_page_map_reasons=inferred is not None)
        pm = dict(q.page_map or {})
        if pdf is None:
            pm["alignment"] = None
        entries = {e["page"]: {**e, "reasons": list(e.get("reasons") or [])} for e in q.pages}
        reasons = list(q.reasons)
        base_score = quality._assess_base(md_marked, probe, n).score
    else:
        base = quality._assess_base(md, probe, n)
        q = None
        pm = None
        entries = {}
        reasons = list(base.reasons)
        base_score = base.score
    if inferred is not None:
        pm = {"method": "inferred", "expected": n, "found": None, "coverage": None,
              "anchored": inferred["anchored"], "decidable": inferred["decidable"],
              "confidence": inferred["confidence"], "undecidable": len(inferred["undecidable"]),
              "out_of_order": inferred["out_of_order"][:pagemap.MISSING_LIST_MAX], "missing": [],
              "invalid_markers": 0, "alignment": None}
        if verifiable is False:
            pm = None if not inferred["anchored"] else pm
    metrics = dict(q.metrics if q is not None else quality._assess_base(md, probe, n).metrics)
    metrics["images_missing"] = md.count(IMAGE_MISSING)
    timing["assess"] = round(time.perf_counter() - t, 3)

    # ---- verify's own page checks (PDF needed): page_dropped + advisories
    if page_level:
        _, secs = pagemap.split_pages(md_marked)
        missing = set((pm or {}).get("missing") or [])
        if pdf is not None:
            blank = set(probe.blank_pages)
            for p in range(1, n + 1):
                if p in missing or p in blank or p not in secs:
                    continue
                sec = secs[p]
                layer_chars = len(layers[p - 1])
                sec_chars = _section_chars(sec)
                if layer_chars >= DROPPED_LAYER_MIN and sec_chars < DROPPED_SECTION_MAX:
                    _merge(entries, p, reason="page_dropped",
                           metrics={"layer_chars": layer_chars, "section_chars": sec_chars})
                    continue
                f = feats[p - 1]
                if f["ruled_grid"] and not quality.has_table(sec):
                    _merge(entries, p, advisory="table_missing_on_page")
                if f["math"] and not _DOLLAR.search(sec):
                    _merge(entries, p, advisory="formula_missing_on_page")
        for p, sec in secs.items():
            if IMAGE_MISSING in sec and 1 <= p <= n:
                _merge(entries, p, advisory="images_missing")

    # ---- level and score (spec 2026-10-01 §5.2 rules)
    page_list = [entries[p] for p in sorted(entries)]
    for e in page_list:
        if not e["reasons"]:
            e.pop("reasons")
    flagged = [e for e in page_list if e.get("reasons")]
    unrepaired = sum(1 for e in flagged if not e.get("repaired_by"))
    if pm is not None and pm.get("found") is not None:
        unrepaired += max(0, (pm["expected"] - pm["found"]) - len(pm.get("missing") or []))
    advisories = sum(len(e.get("advisories") or []) for e in page_list)
    level = "low" if reasons else ("warn" if unrepaired else "ok")
    effective = max(1, n - len(probe.blank_pages))
    coverage = pm["coverage"] if pm is not None and pm.get("coverage") is not None else 1.0
    score = round(base_score * coverage * max(0.0, 1 - unrepaired / effective), 3)
    summary = {"level": level, "score": score, "reasons": reasons, "pages_flagged": len(flagged),
               "pages_unrepaired": unrepaired, "advisories": advisories}
    timing["total"] = round(time.perf_counter() - t0, 3)
    return VerifyReport(verify_version=VERIFY_VERSION, aidoc_version=__version__, generated_at=_now(),
                        input=input_info, pdf=pdf_info, verifiable=verifiable, page_map=pm, summary=summary,
                        metrics=metrics, pages=page_list, previous=previous, timing_s=timing)


def exit_code(report: VerifyReport, fail_on: str = "low", allow_unverifiable: bool = False) -> int:
    """Spec §2.5: 0 passed, 1 failed `--fail-on`, 3 unverifiable (0 with --allow-unverifiable)."""
    if report.verifiable is False:
        return EXIT_OK if allow_unverifiable else EXIT_UNVERIFIABLE
    bad = {"low"} if fail_on == "low" else {"low", "warn"}
    return EXIT_FAILED if report.level in bad else EXIT_OK


def summary_text(report: VerifyReport, lang: str = "en") -> str:      # filled in by Task 4
    return f"level: {report.level}   score: {report.summary['score']}"
