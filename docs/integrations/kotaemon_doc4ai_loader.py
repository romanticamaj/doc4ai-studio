"""Doc4AI Studio reader for Kotaemon (reference implementation, compat spec §8.3).

Kotaemon's own DoclingReader runs docling in-process; this reader sends the file to a Doc4AI Studio server through
its docling-serve compatible API instead (routing, per-page quality checks and OCR repair happen there) and returns
one Kotaemon Document per page.

Install (no Kotaemon patch needed):
  1. Put this file on Kotaemon's PYTHONPATH (e.g. next to flowsettings.py) as `doc4ai_loader.py`.
  2. In flowsettings.py:  FILE_INDEX_PIPELINE_FILE_EXTRACTORS = {".pdf": "doc4ai_loader.Doc4AIReader"}
     (add ".docx", ".pptx", ... the same way if Doc4AI should handle those too)
  3. Environment:  DOC4AI_URL=http://<doc4ai-host>:<port>   DOC4AI_API_KEY=doc4ai_pat_...  (scope doc4ai:convert)

Request shape (replayed by tests/compat/shapes/kotaemon_doc4ai_loader.json):
  POST {DOC4AI_URL}/v1/convert/file, multipart field `files`, form md_page_break_placeholder=\\f, header X-Api-Key.
"""
from __future__ import annotations

import os
from pathlib import Path

import requests

try:  # Kotaemon
    from kotaemon.base import Document
    from kotaemon.loaders.base import BaseReader
except ImportError:  # pragma: no cover  lets the module be imported (and tested) without Kotaemon
    BaseReader = object

    class Document(dict):
        def __init__(self, text: str = "", metadata: dict | None = None):
            super().__init__(text=text, metadata=metadata or {})

CONVERT_PATH = "/v1/convert/file"
FILE_FIELD = "files"
API_KEY_HEADER = "X-Api-Key"
PAGE_BREAK = "\f"
FORM = {"md_page_break_placeholder": "\f", "image_export_mode": "placeholder"}
TIMEOUT_S = 900                      # Doc4AI answers 504 + task_id after compat.sync_timeout_s (600 by default)


class Doc4AIReader(BaseReader):
    """One Document per page (`page_label` = page number, 1-based; empty pages are skipped but keep the numbering)."""

    def __init__(self, url: str | None = None, api_key: str | None = None, *args, **kwargs):
        self.url = (url or os.environ.get("DOC4AI_URL", "http://127.0.0.1:8765")).rstrip("/")
        self.api_key = api_key or os.environ.get("DOC4AI_API_KEY", "")
        if BaseReader is not object:
            super().__init__(*args, **kwargs)

    def run(self, file_path: str | Path, extra_info: dict | None = None, **kwargs) -> list:
        return self.load_data(file_path, extra_info, **kwargs)

    def load_data(self, file_path: str | Path, extra_info: dict | None = None, **kwargs) -> list:
        path = Path(file_path)
        with open(path, "rb") as f:
            r = requests.post(f"{self.url}{CONVERT_PATH}", files={FILE_FIELD: (path.name, f)}, data=FORM,
                              headers={API_KEY_HEADER: self.api_key}, timeout=TIMEOUT_S)
        r.raise_for_status()
        body = r.json()
        if body.get("status") in ("failure", "skipped"):
            errors = "; ".join(e.get("error_message", "") for e in body.get("errors", [])) or "no error message"
            raise RuntimeError(f"Doc4AI conversion {body.get('status')}: {errors}")
        doc = body.get("document") or {}
        info = doc.get("doc4ai") or {}
        base = {**(extra_info or {}), "file_name": path.name, "file_path": str(path), "doc4ai_doc_id": info.get("doc_id"),
                "doc4ai_engine": info.get("engine"), "doc4ai_quality": (info.get("quality") or {}).get("level")}
        md = doc.get("md_content") or ""
        if PAGE_BREAK not in md:
            return [Document(text=md, metadata=base)]
        return [Document(text=page.strip(), metadata={**base, "page_label": i + 1})
                for i, page in enumerate(md.split(PAGE_BREAK)) if page.strip()]
