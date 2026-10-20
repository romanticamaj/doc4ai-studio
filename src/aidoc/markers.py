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


MARKER_REGEX_MAX = 200


PAGE_NUMBER = r"(\d{1,6})"
_GROUP_FORMS = (r"(\d+)", r"([0-9]+)")
_GROUP_BOUNDED = re.compile(r"\((?:\\d|\[0-9\])\{1,[1-9][0-9]?\}\)")
_WS = {r"\s*": (0, True), r"\s+": (1, True), r"\s": (1, False)}
MARKER_HELP = ("a page marker is literal text around one page number: `[[p {page}]]`, or the regex form "
               r"`\[\[p (\d+)\]\]` (escaped punctuation, \s, \s*, \s+ and one (\d+) group only)")


def _tokens(template: str) -> list[tuple]:
    """("lit", char) | ("ws", min, unbounded) | ("num",). A literal space or tab is a one-character whitespace run."""
    toks: list[tuple] = []
    i, n = 0, len(template)
    if "{page}" in template:                   # template form: everything else is literal
        while i < n:
            if template.startswith("{page}", i):
                toks.append(("num",))
                i += 6
            elif template[i] in " \t":
                toks.append(("ws", 1, True))   # a run of spaces in the template matches one or more spaces / tabs
                while i < n and template[i] in " \t":
                    i += 1
            else:
                toks.append(("lit", template[i]))
                i += 1
        return toks
    while i < n:
        c = template[i]
        m = _GROUP_BOUNDED.match(template, i)
        form = next((f for f in _GROUP_FORMS if template.startswith(f, i)), None)
        if m or form:
            toks.append(("num",))
            i += len(m.group(0)) if m else len(form)
        elif c == "\\":
            if i + 1 >= n:
                raise ValueError(f"page marker ends with a backslash; {MARKER_HELP}")
            ws = template[i:i + 3] if template[i:i + 3] in _WS else template[i:i + 2]
            if ws in _WS:
                toks.append(("ws", *_WS[ws]))
                i += len(ws)
            elif not template[i + 1].isalnum():
                ch = template[i + 1]
                toks.append(("ws", 1, False) if ch in " \t" else ("lit", ch))
                i += 2
            else:
                raise ValueError(f"unsupported page marker escape {template[i:i + 2]!r}; {MARKER_HELP}")
        elif c in ".^$*+?{}[]()|":
            raise ValueError(f"unsupported page marker syntax {c!r} at {i}; {MARKER_HELP}")
        elif c in " \t":
            toks.append(("ws", 1, False))
            i += 1
        else:
            toks.append(("lit", c))
            i += 1
    return toks


def compile_marker(template: str, *, numbered: bool = True) -> re.Pattern:
    r"""A custom page marker as a fixed template (verifier rounds 2-3, finding C: a structural check of a free regex
    was bypassed five ways, and `x\s*\s*\s*(\d+)y` still backtracked for > 40 s on 3,000 spaces; Python's `re` has
    no timeout). Accepted: literal text with one `{page}` placeholder, or the regex subset of literal and
    backslash-escaped punctuation, `\s`, `\s*`, `\s+` and exactly one `(\d+)` / `([0-9]+)` / `(\d{1,k})` group.
    Every run of adjacent whitespace tokens (literal spaces and `\s` forms) becomes ONE `[ \t]{m,}` / `[ \t]{m}`, so
    no two quantifiers can split the same characters; the number is `\d{1,6}`; the pattern starts with a literal,
    so a match attempt only starts there: linear time. `numbered=False` (a separator between pages) takes no group."""
    if not template:
        raise ValueError("page marker template is empty")
    if len(template) > MARKER_REGEX_MAX:
        raise ValueError(f"page marker longer than {MARKER_REGEX_MAX} characters")
    merged: list[tuple] = []
    for t in _tokens(template):
        if t[0] == "ws" and merged and merged[-1][0] == "ws":
            merged[-1] = ("ws", merged[-1][1] + t[1], merged[-1][2] or t[2])
        else:
            merged.append(t)
    if sum(1 for t in merged if t[0] == "num") != (1 if numbered else 0):
        raise ValueError("page marker must have exactly one page number" if numbered
                         else f"page separator takes no page number; {MARKER_HELP}")
    if merged[0][0] != "lit":
        raise ValueError(f"page marker must start with literal text; {MARKER_HELP}")
    out = []
    for t in merged:
        if t[0] == "lit":
            out.append(re.escape(t[1]))
        elif t[0] == "num":
            out.append(PAGE_NUMBER)
        else:
            out.append("[ \\t]{" + str(t[1]) + (",}" if t[2] else "}"))
    return re.compile("".join(out))


def _info(fmt: str, found: int, numbered: bool, applied: bool, reason: str | None) -> dict:
    return {"format": fmt, "markers_found": found, "numbered": numbered, "applied": applied, "reason": reason}


def to_aidoc(md: str, fmt: str, *, expected_pages: int | None,
             marker_regex: str | None = None) -> tuple[str, dict]:
    """(Markdown with `<!-- page: N -->` markers, info). `info.applied` is False when the markers cannot be trusted
    as page boundaries; the Markdown is then returned unchanged."""
    if fmt == "none":
        return md, _info(fmt, 0, False, False, "forced_none")
    if fmt == "custom" or (fmt == "auto" and marker_regex):
        if not marker_regex:
            raise ValueError("custom format needs --page-marker-regex (a page marker template)")
        pat, numbered, fmt = compile_marker(marker_regex), True, "custom"
    elif fmt == "docling" and marker_regex:
        pat, numbered = compile_marker(marker_regex, numbered=False), False
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
