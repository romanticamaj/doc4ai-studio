"""VLM OCR runner (spec 2026-10-05 VLM §4). Runs on the HOST interpreter: stdlib + pymupdf + the sibling
_proto.py / _vlm_prompt.py only — never import aidoc here.

One page = one Chat Completions request (data-URI image + prompt) to an OpenAI-compatible endpoint (`base_url`,
backend "openai"; the local llama.cpp backend is not delivered yet — spike S1 rule 4).
Every page gets a `<!-- page: k -->` marker (page_map_method "vlm_per_page"); refusals and truncations are reported
as failed_pages; usage goes to result.json["vlm"].

Endpoint quirks handled here (spike S2, docs/superpowers/spikes/2026-10-vlm-ocr.md): `max_tokens` and
`max_completion_tokens` are both sent (local servers know only the first); `detail` is sent only when configured;
`usage` may be missing; a whole page may come back wrapped in a ```markdown fence; LaTeX may use \\( \\) delimiters.
"""
from __future__ import annotations

import base64
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).parent))
import _proto
import _vlm_prompt

REMOTE_RETRY_BACKOFF = (2, 8, 30, 60)
REMOTE_MAX_RETRIES = 4
RETRY_AFTER_CAP_S = 120
TRUNCATION_RETRY_FACTOR = 2
MAX_OUTPUT_TOKENS_CAP = 16384
BAD_REQUEST_DPI_STEP = 50
MIN_DPI = 72
MAX_IMAGE_BYTES = 15 * 1024 * 1024
MODELS_TIMEOUT_S = 10

_sleep = time.sleep                     # tests replace it


def _now() -> float:
    return time.monotonic()


# ---------------------------------------------------------------- rendering

def render_page(doc, index: int, dpi: int, max_px: int, max_bytes: int = MAX_IMAGE_BYTES) -> tuple[bytes, tuple[int, int], str]:
    """Page `index` of an open PyMuPDF document as PNG (JPEG q=85 when the PNG is over max_bytes); the long side is
    at most max_px. Returns (bytes, (w, h), mime)."""
    import pymupdf
    page = doc[index]
    rect = page.rect
    zoom = dpi / 72.0
    native = getattr(doc, "_aidoc_native_px", None)        # images: never upscale past the native resolution
    long_pt = max(rect.width, rect.height) or 1.0
    if native:
        zoom = min(zoom, native / long_pt)
    if long_pt * zoom > max_px:
        zoom = max_px / long_pt
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    data, mime = pix.tobytes("png"), "image/png"
    if len(data) > max_bytes:
        data, mime = pix.tobytes("jpeg", jpg_quality=85), "image/jpeg"
    return data, (pix.width, pix.height), mime


def open_source(src: str, kind: str):
    """A PyMuPDF document for a PDF or an image (an image becomes a one-page PDF; its native pixel size is kept so
    rendering never upscales it)."""
    import pymupdf
    if kind == "image":
        img = pymupdf.open(src)
        try:
            pix = pymupdf.Pixmap(src)
            native = max(pix.width, pix.height)
        except Exception:  # noqa: BLE001 - unusual formats: no native size, the dpi rule applies
            native = None
        doc = pymupdf.open("pdf", img.convert_to_pdf())
        img.close()
        doc._aidoc_native_px = native
        return doc
    return pymupdf.open(src)


# ---------------------------------------------------------------- cleaning

_FENCE_RE = re.compile(r"^\s*```[ \t]*(?:markdown|md|gfm)?[ \t]*\n(.*?)\n?```\s*$", re.DOTALL | re.IGNORECASE)
_TRAILER_RE = re.compile(
    r"(?:\n|^)[ \t]*(?:Let me know if[^\n]*|Is there anything else[^\n]*|I hope this helps[^\n]*|"
    r"Feel free to ask[^\n]*|希望這對您有幫助[^\n]*|希望对您有帮助[^\n]*|如果您還需要[^\n]*|如有其他需要[^\n]*)\s*$", re.IGNORECASE)
_REFUSAL_RE = re.compile(
    r"^\s*(?:I['\u2019]m sorry|I am sorry|Sorry,|I cannot|I can['\u2019]t|I can not|I['\u2019]m unable|I am unable|"
    r"I['\u2019]m not able|I am not able|I won['\u2019]t|I will not|Unfortunately|As an AI|"
    r"無法|抱歉|很抱歉|對不起|对不起|无法)", re.IGNORECASE)
REFUSAL_MAX_CHARS = 200


def clean_markdown(text: str) -> tuple[str, str | None]:
    """(cleaned markdown, flag) — flag is None, "blank" or "refused" (spec §4.4)."""
    s = (text or "").strip()
    m = _FENCE_RE.match(s)
    if m:
        s = m.group(1).strip()
    while True:
        t = _TRAILER_RE.sub("", s).rstrip()
        if t == s:
            break
        s = t
    if s in ("<!-- blank -->", ""):
        return "", "blank"
    if len(s) < REFUSAL_MAX_CHARS and _REFUSAL_RE.match(s):
        return "", "refused"
    s = re.sub(r"\\\[(.+?)\\\]", lambda mm: f"$${mm.group(1).strip()}$$", s, flags=re.DOTALL)
    s = re.sub(r"\\\((.+?)\\\)", lambda mm: f"${mm.group(1).strip()}$", s, flags=re.DOTALL)
    return s, None


_OTSL_CELL = re.compile(r"<(fcel|ecel|lcel|ucel|xcel|ched|rhed|srow)>")


def otsl_to_gfm(text: str) -> str | None:
    """PaddleOCR-VL "Table Recognition:" answers in OTSL tokens (<fcel>cell … <nl>); a GFM pipe table, or None when
    the answer is not OTSL. Spanned cells (<lcel>/<ucel>/<xcel>) repeat the neighbouring text (spec §4.4 rule)."""
    if "<nl>" not in text or not _OTSL_CELL.search(text):
        return None
    rows: list[list[str]] = []
    for raw_row in text.split("<nl>"):
        raw_row = raw_row.strip()
        if not raw_row:
            continue
        cells: list[tuple[str, str]] = []
        for part in re.split(r"(?=<(?:fcel|ecel|lcel|ucel|xcel|ched|rhed|srow)>)", raw_row):
            m = re.match(r"<(fcel|ecel|lcel|ucel|xcel|ched|rhed|srow)>(.*)", part, re.DOTALL)
            if m:
                cells.append((m.group(1), m.group(2).strip()))
        row: list[str] = []
        for i, (tag, val) in enumerate(cells):
            if tag == "lcel":
                row.append(row[-1] if row else "")
            elif tag in ("ucel", "xcel"):
                above = rows[-1][i] if rows and i < len(rows[-1]) else ""
                row.append(above)
            else:
                row.append(val)
        rows.append(row)
    if not rows:
        return None
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]

    def line(r):
        return "| " + " | ".join(c.replace("|", "\\|").replace("\n", " ") for c in r) + " |"
    return "\n".join([line(rows[0]), "| " + " | ".join(["---"] * width) + " |", *(line(r) for r in rows[1:])])


def mark_missing_images(md: str) -> tuple[str, int]:
    """`<!-- image -->` lines cannot be cropped (no layout boxes): keep them as `<!-- image missing -->` (spec §4.5)."""
    n = 0

    def repl(_m):
        nonlocal n
        n += 1
        return "<!-- image missing -->"
    return re.sub(r"<!--\s*image\s*-->", repl, md), n


# ---------------------------------------------------------------- HTTP client

class HttpStatus(Exception):
    def __init__(self, code: int, retry_after: float | None, body: str):
        super().__init__(f"HTTP {code}: {body[:300]}")
        self.code, self.retry_after, self.body = code, retry_after, body


def parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


class Client:
    def __init__(self, base_url: str, model: str, api_key: str | None, timeout_s: float, detail: str | None,
                 max_output_tokens: int):
        self.base_url = base_url.rstrip("/")
        self.model, self.api_key, self.timeout_s = model, api_key, timeout_s
        self.detail, self.max_output_tokens = detail, max_output_tokens

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _open(self, req, timeout):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                body = ""
            raise HttpStatus(e.code, parse_retry_after(e.headers.get("Retry-After") if e.headers else None),
                             body) from None

    def chat(self, messages: list[dict], max_tokens: int) -> dict:
        body = {"model": self.model, "messages": messages, "temperature": 0, "max_tokens": max_tokens,
                "max_completion_tokens": max_tokens, "stream": False}
        req = urllib.request.Request(self.base_url + "/chat/completions", data=json.dumps(body).encode("utf-8"),
                                     headers=self._headers(), method="POST")
        return self._open(req, self.timeout_s)

    def models(self) -> list[str]:
        req = urllib.request.Request(self.base_url + "/models", headers=self._headers(), method="GET")
        out = self._open(req, MODELS_TIMEOUT_S)
        return [str(m.get("id")) for m in (out.get("data") or []) if isinstance(m, dict)]


# ---------------------------------------------------------------- one page

@dataclass
class PageOut:
    markdown: str = ""
    flag: str | None = None               # None | "blank" | "refused" | "truncated" | "bad_request"
    finish_reason: str | None = None
    usage: dict | None = None
    attempts: int = 0
    dpi_used: int = 0
    images_missing: int = 0
    extra_usage: list = field(default_factory=list)


def _add_usage(acc: dict | None, u: dict | None) -> dict | None:
    if not u:
        return acc
    acc = dict(acc or {"prompt_tokens": 0, "completion_tokens": 0})
    acc["prompt_tokens"] += int(u.get("prompt_tokens") or 0)
    acc["completion_tokens"] += int(u.get("completion_tokens") or 0)
    return acc


def _host_of(client: Client) -> str:
    return urlsplit(client.base_url).netloc


def request_with_retries(client: Client, messages: list[dict], max_tokens: int, stats: dict) -> dict:
    """One chat request with the spec §4.4 retry schedule. Raises HttpStatus(400) for the caller to re-render,
    RunnerError("engine") for 401/403/404, RunnerError("transient") when the retries are used up."""
    last = ""
    for attempt in range(REMOTE_MAX_RETRIES + 1):
        stats["requests"] = stats.get("requests", 0) + 1
        try:
            return client.chat(messages, max_tokens)
        except HttpStatus as e:
            if e.code in (401, 403, 404):
                raise _proto.RunnerError(
                    "engine", f"vlm: HTTP {e.code} from {_host_of(client)} (check base_url, model and API key): "
                              f"{_error_message(e.body)}") from None
            if e.code == 400:
                raise
            if e.code != 429 and e.code < 500:
                raise _proto.RunnerError("engine", f"vlm: HTTP {e.code} from {_host_of(client)}: "
                                                   f"{_error_message(e.body)}") from None
            last = f"HTTP {e.code}"
            delay = REMOTE_RETRY_BACKOFF[min(attempt, len(REMOTE_RETRY_BACKOFF) - 1)]
            if e.code == 429 and e.retry_after is not None:
                delay = min(RETRY_AFTER_CAP_S, e.retry_after)
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            last = f"{type(e).__name__}: {getattr(e, 'reason', e)}"
            delay = REMOTE_RETRY_BACKOFF[min(attempt, len(REMOTE_RETRY_BACKOFF) - 1)]
        if attempt == REMOTE_MAX_RETRIES:
            break
        _proto.log(f"vlm: {last}; retry {attempt + 1}/{REMOTE_MAX_RETRIES} in {delay:g}s")
        _sleep(delay)
    raise _proto.RunnerError("transient", f"vlm: {_host_of(client)} failed after {REMOTE_MAX_RETRIES} retries: {last}")


def _error_message(body: str) -> str:
    try:
        err = json.loads(body).get("error")
        if isinstance(err, dict):
            return str(err.get("message") or err.get("code") or "")[:300]
        return str(err)[:300]
    except (ValueError, AttributeError):
        return body[:300]


def _content(out: dict) -> tuple[str, str | None]:
    try:
        ch = out["choices"][0]
        return str(ch["message"].get("content") or ""), ch.get("finish_reason")
    except (KeyError, IndexError, TypeError, AttributeError):
        raise _proto.RunnerError("transient", f"vlm: malformed response: {str(out)[:200]}") from None


def send_page(client: Client, render, k: int, n: int, opts: dict, stats: dict) -> PageOut:
    """Convert page k of n. `render(dpi) -> (bytes, (w, h), mime)`."""
    dpi = int(opts.get("dpi", 150))
    style = opts.get("prompt_style") or "generic"
    lang = opts.get("lang") or "cht"
    max_tokens = int(opts.get("max_output_tokens", 4096))
    res = PageOut(dpi_used=dpi)
    rerendered = False
    while True:
        data, _size, mime = render(dpi)
        uri = f"data:{mime};base64," + base64.b64encode(data).decode("ascii")
        msgs = _vlm_prompt.messages(style, lang, uri, k, n, client.detail)
        try:
            out = request_with_retries(client, msgs, max_tokens, stats)
        except HttpStatus as e:                 # 400: often "image too large" -> one re-render at a lower dpi
            res.attempts += 1
            if rerendered or dpi - BAD_REQUEST_DPI_STEP < MIN_DPI:
                res.flag = "bad_request"
                _proto.log(f"vlm: page {k}: HTTP 400 ({_error_message(e.body)}); page skipped")
                return res
            dpi, rerendered, res.dpi_used = dpi - BAD_REQUEST_DPI_STEP, True, dpi - BAD_REQUEST_DPI_STEP
            _proto.log(f"vlm: page {k}: HTTP 400; retrying at {dpi} dpi")
            continue
        res.attempts += 1
        res.usage = _add_usage(res.usage, out.get("usage"))
        if out.get("usage"):
            stats["has_usage"] = True
        text, finish = _content(out)
        res.finish_reason = finish
        if finish not in (None, "stop", "length", "eos", "end_turn"):   # content_filter & co: the model declined
            res.flag, res.markdown = "refused", ""
            return res
        if finish == "length" and max_tokens < MAX_OUTPUT_TOKENS_CAP and not res.flag:
            max_tokens = min(MAX_OUTPUT_TOKENS_CAP, max_tokens * TRUNCATION_RETRY_FACTOR)
            res.flag = "retrying_length"
            _proto.log(f"vlm: page {k}: output truncated; retrying with max_tokens={max_tokens}")
            continue
        md, flag = clean_markdown(text)
        if finish == "length":
            res.flag = "truncated"              # keep the partial text: some content beats an empty page
            res.markdown = md
            return res
        res.flag = flag
        res.markdown = md
        if flag is None and style == "paddle_tasks" and k in set(opts.get("table_pages") or []):
            res.markdown = _with_table(client, data, mime, md, k, n, opts, stats, res)
        return res


def _with_table(client, data, mime, md, k, n, opts, stats, res) -> str:
    """Two-pass table mode (spike S1, rule 1 table clause): a second "Table Recognition:" request on a page the
    probe saw a ruled table on; the OTSL answer becomes a GFM table appended after the page text."""
    uri = f"data:{mime};base64," + base64.b64encode(data).decode("ascii")
    msgs = _vlm_prompt.messages("paddle_tasks", opts.get("lang") or "cht", uri, k, n, client.detail, task="table")
    try:
        out = request_with_retries(client, msgs, int(opts.get("max_output_tokens", 4096)), stats)
    except HttpStatus:
        return md
    res.usage = _add_usage(res.usage, out.get("usage"))
    text, finish = _content(out)
    table = otsl_to_gfm(text) if finish != "length" else None
    return f"{md}\n\n{table}" if table else md


# ---------------------------------------------------------------- request handler

_LOCAL: dict = {}                          # backend "local": {"base_url": ...} set by main() once the server is up
_MODELS_CHECKED: set = set()


def resolve_key(eo: dict) -> str | None:
    env_name = eo.get("api_key_env") or ""
    if env_name and os.environ.get(env_name):
        return os.environ[env_name]
    return os.environ.get("AIDOC_VLM_API_KEY_INLINE") or None


def _is_loopback(host: str) -> bool:
    """`host[:port]` names this computer: localhost or a loopback IP (not merely a name starting with "127.")."""
    import ipaddress
    h = host.rsplit(":", 1)[0] if host.count(":") == 1 or host.startswith("[") else host
    h = h.strip("[]").lower()
    if h == "localhost":
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def handle(req: dict) -> dict:
    eo = dict(req.get("engine_opts") or {})
    backend = eo.get("backend", "openai")
    if backend == "local":
        base_url = eo.get("base_url") or _LOCAL.get("base_url")
        if not base_url:
            raise _proto.RunnerError("transient", "vlm: local llama-server is not running")
        key, model = None, eo.get("preset") or "local"
    elif backend == "openai":
        base_url, model = eo.get("base_url") or "", eo.get("model") or ""
        key = resolve_key(eo)
        if key is None and not eo.get("allow_no_key"):
            raise _proto.RunnerError("engine", f"vlm: no API key: set the environment variable "
                                               f"{eo.get('api_key_env') or 'AIDOC_VLM_API_KEY'} (or allow_no_key)")
    else:
        raise _proto.RunnerError("engine", f"vlm: backend {backend!r} is not configured")
    client = Client(base_url, model, key, float(eo.get("request_timeout_s", 120)), eo.get("detail"),
                    int(eo.get("max_output_tokens", 4096)))
    host = urlsplit(client.base_url).netloc
    if backend == "openai" and base_url not in _MODELS_CHECKED:
        _MODELS_CHECKED.add(base_url)
        try:
            ids = client.models()
            if model not in ids:
                _proto.log(f"vlm: model {model!r} is not in {host}/models ({len(ids)} listed); trying anyway")
        except Exception as e:  # noqa: BLE001 - informational only (spec §4.7)
            _proto.log(f"vlm: GET {host}/models failed ({type(e).__name__}); trying the first page anyway")

    out_dir = Path(req["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = open_source(req["src"], req.get("kind") or "pdf")
    render_lock = threading.Lock()
    n = doc.page_count
    max_px = int(eo.get("max_image_px", 2000))
    stats: dict = {"requests": 0}
    results: list[PageOut | None] = [None] * n
    done = [0]

    def run_one(i: int) -> None:
        base_dpi = int(eo.get("dpi", 150))

        def render(dpi):
            # a lower dpi must give a smaller image even when the long-side cap decides the size (400 re-render)
            cap = max_px if dpi >= base_dpi else max(256, int(max_px * dpi / base_dpi))
            with render_lock:
                return render_page(doc, i, dpi, cap)
        try:
            results[i] = send_page(client, render, i + 1, n, eo, stats)
        except _proto.RunnerError as e:
            if e.kind != "transient":
                raise                           # 401/403/404: a configuration error, the whole run fails
            _proto.log(f"vlm: page {i + 1}: {e}")
            results[i] = PageOut(flag="unreachable")   # retries used up: this page only (spec §4.4 page level)
        with render_lock:
            done[0] += 1
            _proto.progress(done[0], n)

    workers = max(1, min(int(eo.get("max_concurrent", 1)), 8))
    try:
        if workers == 1:
            for i in range(n):
                run_one(i)
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(run_one, i) for i in range(n)]
                try:
                    for f in futures:
                        f.result()
                except BaseException:
                    for f in futures:
                        f.cancel()
                    raise
    finally:
        doc.close()
    if n and all(r is not None and r.flag == "unreachable" for r in results):
        raise _proto.RunnerError("transient", f"vlm: {host} did not answer for any of the {n} page(s)")

    parts, failed, refused, truncated, images_missing = [], {}, [], [], {}
    usage = None
    for i, r in enumerate(results):
        k = i + 1
        r = r or PageOut(flag="bad_request")
        md, n_missing = mark_missing_images(r.markdown)
        if n_missing:
            images_missing[str(k)] = n_missing
        parts.append(f"<!-- page: {k} -->\n\n{md}\n\n" if md else f"<!-- page: {k} -->\n\n")
        usage = _add_usage(usage, r.usage)
        if r.flag == "refused":
            failed[str(k)] = "refused"
            refused.append(k)
        elif r.flag == "truncated":
            failed[str(k)] = "truncated"
            truncated.append(k)
        elif r.flag in ("bad_request", "unreachable"):
            failed[str(k)] = r.flag
    md_path = out_dir / "out.md"
    md_path.write_text("".join(parts), encoding="utf-8")
    vlm = {"backend": backend, "model": model, "endpoint_host": "local" if backend == "local" else host,
           "local_endpoint": backend == "local" or _is_loopback(host), "pages_sent": n,
           "usage": {"requests": stats["requests"],
                     "prompt_tokens": usage["prompt_tokens"] if usage else None,
                     "completion_tokens": usage["completion_tokens"] if usage else None},
           "truncated_pages": truncated, "refused_pages": refused, "dpi": int(eo.get("dpi", 150)),
           "prompt_version": _vlm_prompt.PROMPT_VERSION}
    if backend == "local":
        vlm["preset"] = eo.get("preset")
    return {"markdown_path": str(md_path), "images": [], "has_page_markers": True, "page_count": n,
            "page_map_method": "vlm_per_page", "first_table": None, "last_table": None,
            "failed_pages": failed, "errors": [f"page {k}: {v}" for k, v in failed.items()],
            "images_missing": images_missing, "vlm": vlm}


def main() -> None:
    _proto.serve(handle)


if __name__ == "__main__":
    main()
