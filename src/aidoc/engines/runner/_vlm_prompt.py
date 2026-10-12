"""VLM prompts (spec 2026-10-05 VLM §4.3–§4.4). STDLIB ONLY (imported by vlm_runner.py on the host interpreter).

Changing any text here changes the engine's output style: bump PROMPT_VERSION, update PINNED_SHA256
(tests/unit/test_vlm_runner.py pins it) and re-run the benchmark.
"""
from __future__ import annotations

import hashlib
import json

PROMPT_VERSION = 1

_BASE = (
    "You convert one page image of a document into GitHub-Flavored Markdown. Output only the Markdown of the page "
    "content, nothing else: no preface, no code fence around the whole page, no commentary. Keep the page's language "
    "and script exactly as printed (Traditional Chinese stays Traditional Chinese; never translate). Preserve reading "
    "order (left column before right column). Headings -> #/##/###, tables -> GFM pipe tables (one header row; merged "
    "cells repeated), formulas -> LaTeX in $…$ or $$…$$, figures/photos/charts -> the single line <!-- image --> "
    "followed by the caption if there is one. Page headers, footers and page numbers -> omit. If the page is blank or "
    "has no text, output exactly <!-- blank -->.")

SYSTEM = {
    "en": _BASE,
    "cht": _BASE + " 這是繁體中文文件：照原樣保留繁體字，不要改成簡體字，也不要翻譯。",
}
USER = "Page {k} of {n}. Return the Markdown of this page."
# PaddleOCR-VL task prompts (HF model card): one user message = image + task text, no system message
PADDLE_TASKS = {"ocr": "OCR:", "table": "Table Recognition:", "formula": "Formula Recognition:"}
PROMPT_STYLES = ("paddle_tasks", "generic")

PINNED_SHA256 = "e7113f206322360846d9e6516ffc3fea10865392903eeedafbc7618358f1a6ea"


def prompt_sha256() -> str:
    blob = json.dumps({"v": PROMPT_VERSION, "system": SYSTEM, "user": USER, "paddle": PADDLE_TASKS},
                      ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def messages(style: str, lang: str, data_uri: str, k: int, n: int, detail: str | None, task: str = "ocr") -> list[dict]:
    image = {"type": "image_url", "image_url": {"url": data_uri}}
    if detail:
        image["image_url"]["detail"] = detail
    if style == "paddle_tasks":
        return [{"role": "user", "content": [image, {"type": "text", "text": PADDLE_TASKS[task]}]}]
    return [{"role": "system", "content": SYSTEM.get(lang, SYSTEM["en"])},
            {"role": "user", "content": [image, {"type": "text", "text": USER.format(k=k, n=n)}]}]
