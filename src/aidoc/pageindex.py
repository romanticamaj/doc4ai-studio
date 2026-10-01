"""Per-page full-text index for `search_library` (MCP spec §3 `page_index`). Pages come from `<!-- page: N -->`
markers (aidoc.pagemap); documents without markers are one row with page NULL."""
from __future__ import annotations

import re
import threading
from collections.abc import Callable
from pathlib import Path

from aidoc.pagemap import split_pages

MIN_TRIGRAM_CHARS = 3
_WS = re.compile(r"\s+")


def pages_for_index(markdown: str) -> list[tuple[int | None, str]]:
    if not markdown.strip():
        return []
    pre, secs = split_pages(markdown)
    if not secs:
        return [(None, markdown)]
    out = []
    for page in sorted(secs):
        text = secs[page]
        if page == min(secs) and pre.strip():
            text = pre + text
        out.append((page, text))
    return out


def _markdown_path(doc: dict) -> Path:
    out = Path(doc["output_dir"])
    return out / f"{out.name}.md"


def index_document(store, doc: dict) -> int:
    """Rewrite the index rows of one document from its Markdown; 0 rows when the output is gone."""
    md = _markdown_path(doc)
    try:
        text = md.read_text(encoding="utf-8")
    except OSError:
        store.delete_page_index(doc["id"])
        return 0
    pages = pages_for_index(text)
    store.replace_page_index(doc["id"], pages)
    return len(pages)


_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def clean_query(q: str) -> str:
    """User text made safe for SQLite: control characters (NUL ends an FTS5 string) become spaces and lone
    surrogates (unencodable as UTF-8) are dropped."""
    return _CONTROL.sub(" ", q.encode("utf-8", "ignore").decode("utf-8"))


def _terms(q: str) -> list[str]:
    return [t for t in _WS.split(clean_query(q).strip()) if t]


def fts_query(q: str) -> str | None:
    """Every whitespace-separated term as a quoted FTS5 string (user text is never FTS syntax); terms shorter than
    three characters are dropped because the trigram tokenizer cannot match them (D6)."""
    terms = [t for t in _terms(q) if len(t) >= MIN_TRIGRAM_CHARS]
    if not terms:
        return None
    return " ".join('"' + t.replace('"', '""') + '"' for t in terms)


def search_pages(store, query: str, *, doc_ids: list[str] | None = None, limit: int = 50) -> list[dict]:
    terms = _terms(query)
    if not terms:
        return []
    match = fts_query(query)
    if match is not None:
        return store.search_page_index(match, doc_ids=doc_ids, limit=limit)
    # D6 fallback: only short terms (e.g. 兩個字) — substring scan, longest term first
    needle = max(terms, key=len)
    return store.like_page_index(needle, doc_ids=doc_ids, limit=limit)


def make_snippet(text: str, query: str, width: int = 300) -> str:
    flat = _WS.sub(" ", text).strip()
    if len(flat) <= width:
        return flat
    low = flat.lower()
    pos = -1
    for t in sorted(_terms(query), key=len, reverse=True):
        pos = low.find(t.lower())
        if pos >= 0:
            break
    if pos < 0:
        return flat[:width].rstrip() + "…"
    start = max(0, pos - width // 2)
    end = min(len(flat), start + width)
    start = max(0, end - width)
    out = flat[start:end].strip()
    return ("…" if start > 0 else "") + out + ("…" if end < len(flat) else "")


def backfill_page_index(store, log: Callable[[str], None] = print) -> int:
    done = store.page_index_doc_ids()
    n = 0
    for doc in store.list_documents():
        if doc["id"] in done or doc["status"] not in ("ok", "warn", "low"):
            continue
        rows = index_document(store, doc)
        if rows:
            n += 1
            log(f"indexed {Path(doc['output_dir']).name}: {rows} pages")
    return n


def start_page_index_backfill(store, log: Callable[[str], None] | None = None) -> threading.Thread:
    """Background backfill at server start (spec §3): never blocks startup; errors are logged, not raised."""
    import sys

    def emit(line: str) -> None:
        print(f"page_index: {line}", file=sys.stderr, flush=True)

    def run() -> None:
        try:
            backfill_page_index(store, log=log or emit)
        except Exception as e:  # noqa: BLE001  a background helper must never take the server down
            (log or emit)(f"failed: {type(e).__name__}: {e}")
    th = threading.Thread(target=run, name="aidoc-page-index", daemon=True)
    th.start()
    return th
