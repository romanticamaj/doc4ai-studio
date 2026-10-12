"""Page-marker dialects of third-party Markdown (spec 2026-10-05 verify §2.2).

Every dialect is rewritten to our `<!-- page: N -->` view so `verify` can reuse `pagemap` / `quality` unchanged.
Numbered dialects are renamed one to one. Unnumbered dialects (a fixed separator between pages) are numbered 1..k
only when k equals the PDF's page count — anything else would silently shift pages, so the caller falls back to
`pagemap.infer_pages()` instead.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from aidoc.pagemap import MARKER


@dataclass(frozen=True)
class Dialect:
    name: str
    pattern: re.Pattern | None
    numbered: bool
    note: str


DIALECTS: dict[str, Dialect] = {
    "aidoc": Dialect("aidoc", MARKER, True, "Doc4AI output: one `<!-- page: N -->` per page"),
    "pdfmux": Dialect("pdfmux", re.compile(r"<!--\s*page\s+(\d+)\s*-->", re.IGNORECASE), True,
                      "pdfmux-style `<!-- page N -->`"),
    "docling": Dialect("docling", re.compile(r"<!-- page break -->"), False,
                       "Docling export_to_markdown(page_break_placeholder=...) default placeholder; blank pages "
                       "may be dropped (docling #2623)"),
    "mineru": Dialect("mineru", re.compile(r"(?m)^---[ \t]*$"), False,
                      "MinerU ParseResult.markdown(add_markers=True): `---` between pages, no page numbers"),
    "markitdown": Dialect("markitdown", re.compile(r"<!-- Slide number: (\d+) -->"), True,
                          "PDF output has no markers; pptx slide markers are recorded but never used for a PDF"),
}
DETECT_ORDER = ("aidoc", "pdfmux", "docling", "mineru")
FORMATS = ("auto", "aidoc", "mineru", "docling", "markitdown", "pdfmux", "none", "custom")


def detect(md: str) -> str:
    """First dialect whose pattern occurs at least twice, else "none"."""
    for name in DETECT_ORDER:
        pat = DIALECTS[name].pattern
        if pat is not None and len(pat.findall(md)) >= 2:
            return name
    return "none"


def _compile_custom(marker_regex: str | None) -> re.Pattern:
    if not marker_regex:
        raise ValueError("custom format needs --page-marker-regex")
    try:
        pat = re.compile(marker_regex)
    except re.error as e:
        raise ValueError(f"invalid page marker regex: {e}") from None
    if pat.groups != 1:
        raise ValueError("page marker regex must have exactly one group (the 1-based page number)")
    return pat


def _info(fmt: str, found: int, numbered: bool, applied: bool, reason: str | None) -> dict:
    return {"format": fmt, "markers_found": found, "numbered": numbered, "applied": applied, "reason": reason}


def to_aidoc(md: str, fmt: str, *, expected_pages: int | None,
             marker_regex: str | None = None) -> tuple[str, dict]:
    """(Markdown with `<!-- page: N -->` markers, info). `info.applied` is False when the markers cannot be trusted
    as page boundaries; the Markdown is then returned unchanged."""
    if fmt == "none":
        return md, _info(fmt, 0, False, False, "forced_none")
    if fmt == "custom" or (fmt == "auto" and marker_regex):
        pat, numbered, fmt = _compile_custom(marker_regex), True, "custom"
    elif fmt == "docling" and marker_regex:
        try:
            pat, numbered = re.compile(marker_regex), False
        except re.error as e:
            raise ValueError(f"invalid page marker regex: {e}") from None
    elif fmt in DIALECTS:
        d = DIALECTS[fmt]
        pat, numbered = d.pattern, d.numbered
    else:
        raise ValueError(f"unknown format {fmt!r} (one of {', '.join(FORMATS)})")
    found = len(pat.findall(md))
    if fmt == "markitdown":
        return md, _info(fmt, found, True, False, "markitdown_pdf_has_no_markers")
    if found == 0:
        return md, _info(fmt, 0, numbered, False, "no_markers")
    if fmt == "aidoc":
        return md, _info(fmt, found, True, True, None)
    if numbered:
        out = pat.sub(lambda m: f"<!-- page: {int(m.group(1))} -->", md)
        return out, _info(fmt, found, True, True, None)
    if expected_pages is None:
        return md, _info(fmt, found, False, False, "page_count_unknown")
    parts = pat.split(md)
    if len(parts) != expected_pages:
        return md, _info(fmt, found, False, False, "section_count_mismatch")
    out = "".join(f"<!-- page: {i} -->\n\n{part.strip(chr(10))}\n\n" for i, part in enumerate(parts, 1))
    return out, _info(fmt, found, False, True, None)
