"""A scripted OpenAI-compatible vision endpoint for tests (VLM spec §11.1). stdlib only, no network beyond loopback.

    with FakeVlmServer({"default": "ok", "pages": {"2": "text"}}) as base_url:   # base_url ends in /v1
        ...

Scenario keys: default (behaviour), model, delay_s, pages ({k: text}), with_table, require_key, call_log (path of a
calls.jsonl), rules ([{match: {page, request_no, model}, behavior}]); request_no counts requests for that page.
Behaviours: ok, ok_fenced, blank, refuse, length, rate_limit, server_error, unauthorized, model_missing, bad_request,
slow, hang, no_usage, unauthorized_echo, malformed.
"""
from __future__ import annotations

import base64
import json
import re
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DEFAULT_TEXT = ("假端點產生的內容 fake vlm content for page {k}. 這一頁的文字足夠長，可以通過每頁字數的檢查。"
                "The fake endpoint writes a few ordinary sentences so that the page passes the quality gate.")
_PAGE_RE = re.compile(r"Page (\d+) of (\d+)")
_ERRORS = {
    "rate_limit": (429, "rate_limit_exceeded", "requests", "Rate limit reached"),
    "server_error": (500, "server_error", "server_error", "The server had an error"),
    "unauthorized": (401, "invalid_api_key", "invalid_request_error", "Incorrect API key provided"),
    "model_missing": (404, "model_not_found", "invalid_request_error", "The model does not exist"),
    "bad_request": (400, "invalid_request_error", "invalid_request_error", "Invalid image"),
}


def png_size(data: bytes) -> list[int] | None:
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return None
    w, h = struct.unpack(">II", data[16:24])
    return [w, h]


class ScenarioState:
    """Scenario + per-page request counters + call log; shared by FakeVlmServer and fake_llama_server."""

    def __init__(self, scenario: dict | Path | str):
        if isinstance(scenario, (str, Path)):
            scenario = json.loads(Path(scenario).read_text(encoding="utf-8"))
        self.sc = dict(scenario)
        self.lock = threading.Lock()
        self.counts: dict = {}
        self.calls: list[dict] = []
        self.stopping = threading.Event()

    def behaviour(self, page, model) -> tuple[str, int]:
        with self.lock:
            n = self.counts[page] = self.counts.get(page, 0) + 1
        for rule in self.sc.get("rules", []):
            m = rule.get("match", {})
            if "page" in m and m["page"] != page:
                continue
            if "request_no" in m and m["request_no"] != n:
                continue
            if "model" in m and m["model"] != model:
                continue
            return rule["behavior"], n
        return self.sc.get("default", "ok"), n

    def record(self, call: dict) -> None:
        with self.lock:
            self.calls.append(call)
            log = self.sc.get("call_log")
            if log:
                with open(log, "a", encoding="utf-8") as f:
                    f.write(json.dumps(call, ensure_ascii=False) + "\n")

    def page_text(self, k) -> str:
        pages = self.sc.get("pages") or {}
        text = pages.get(str(k)) if k is not None else None
        return text if text is not None else DEFAULT_TEXT.format(k=k if k is not None else 1)


def _parse(body: dict) -> dict:
    page = total = None
    image_bytes, image_px, detail, prompt, n_images = 0, None, None, "", 0
    for msg in body.get("messages") or []:
        content = msg.get("content")
        parts = content if isinstance(content, list) else [{"type": "text", "text": content or ""}]
        for part in parts:
            if part.get("type") == "text":
                text = part.get("text") or ""
                if msg.get("role") == "user":
                    prompt = text
                m = _PAGE_RE.search(text)
                if m and msg.get("role") == "user":
                    page, total = int(m.group(1)), int(m.group(2))
            elif part.get("type") == "image_url":
                n_images += 1
                iu = part.get("image_url") or {}
                detail = iu.get("detail")
                url = iu.get("url") or ""
                if url.startswith("data:") and "," in url:
                    raw = base64.b64decode(url.split(",", 1)[1])
                    image_bytes, image_px = len(raw), png_size(raw)
    return {"page": page, "total": total, "image_bytes": image_bytes, "image_px": image_px, "detail": detail,
            "prompt": prompt, "n_images": n_images}


def handle_chat(state: ScenarioState, body: dict, headers) -> tuple[int, dict, dict]:
    """(status, json body, extra headers) for one POST /chat/completions."""
    info = _parse(body)
    model = body.get("model")
    auth = headers.get("Authorization") or ""
    state.record({"ts": time.time(), "path": "/v1/chat/completions", "model": model, "page": info["page"],
                  "total": info["total"], "image_bytes": info["image_bytes"], "image_px": info["image_px"],
                  "has_auth": bool(auth), "detail": info["detail"], "max_tokens": body.get("max_tokens"),
                  "max_completion_tokens": body.get("max_completion_tokens"), "prompt": info["prompt"],
                  "temperature": body.get("temperature"), "n_images": info["n_images"]})
    key = state.sc.get("require_key")
    if key and auth != f"Bearer {key}":
        return _error("unauthorized")
    beh, _n = state.behaviour(info["page"], model)
    if beh == "unauthorized_echo":              # some providers echo what they got (verifier finding 3)
        return 401, {"error": {"message": f"Incorrect API key provided: {auth}", "type": "invalid_request_error",
                               "code": "invalid_api_key"}}, {}
    if beh == "malformed":                      # not an OpenAI shape; carries page text
        return 200, {"choices": "CONFIDENTIAL PAGE TEXT " * 20}, {}
    if beh == "hang":
        state.stopping.wait()
        return _error("server_error")
    if beh == "slow":
        time.sleep(float(state.sc.get("delay_s", 1.0)))
        beh = "ok"
    if beh in _ERRORS:
        return _error(beh)
    k = info["page"]
    text = f"# Page {k if k is not None else 1}\n\n{state.page_text(k)}"
    if state.sc.get("with_table"):
        text += "\n\n| a | b |\n| --- | --- |\n| 1 | 2 |"
    finish = "stop"
    if beh == "ok_fenced":
        text = f"```markdown\n{text}\n```"
    elif beh == "blank":
        text = "<!-- blank -->"
    elif beh == "refuse":
        text = "I'm sorry, I can't help with that."
    elif beh == "length":
        text, finish = text[: max(1, len(text) // 2)], "length"
    out = {"id": "chatcmpl-fake", "object": "chat.completion", "created": int(time.time()), "model": model,
           "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": finish}]}
    if beh != "no_usage":
        pt = 100 + info["image_bytes"] // 100
        out["usage"] = {"prompt_tokens": pt, "completion_tokens": len(text), "total_tokens": pt + len(text)}
    return 200, out, {}


def _error(beh: str) -> tuple[int, dict, dict]:
    status, code, typ, msg = _ERRORS[beh]
    extra = {"Retry-After": "1"} if status == 429 else {}
    return status, {"error": {"message": msg, "type": typ, "code": code}}, extra


def make_handler(state: ScenarioState, prefix: str = "/v1"):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):          # keep test output clean
            pass

        def _send(self, status: int, body: dict, extra: dict | None = None) -> None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def do_GET(self):
            if self.path.rstrip("/") == "/health":         # llama-server's readiness probe
                self._send(200, {"status": "ok"})
            elif self.path.rstrip("/") == f"{prefix}/models":
                self._send(200, {"object": "list", "data": [{"id": state.sc.get("model", "fake-vlm"), "object": "model"}]})
            else:
                self._send(404, {"error": {"message": "not found", "type": "invalid_request_error", "code": None}})

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            if self.path.rstrip("/") != f"{prefix}/chat/completions":
                self._send(404, {"error": {"message": "not found", "type": "invalid_request_error", "code": None}})
                return
            try:
                body = json.loads(raw or b"{}")
            except ValueError:
                self._send(400, {"error": {"message": "bad json", "type": "invalid_request_error", "code": None}})
                return
            status, out, extra = handle_chat(state, body, self.headers)
            self._send(status, out, extra)

    return Handler


class FakeVlmServer:
    def __init__(self, scenario: dict | Path | str, *, host: str = "127.0.0.1"):
        self.state = ScenarioState(scenario)
        self.host = host
        self.httpd: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    @property
    def calls(self) -> list[dict]:
        return self.state.calls

    def start(self) -> str:
        self.httpd = ThreadingHTTPServer((self.host, 0), make_handler(self.state))
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        return f"http://{self.host}:{self.httpd.server_address[1]}/v1"

    def stop(self) -> None:
        self.state.stopping.set()
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None

    def __enter__(self) -> str:
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()


def main(argv=None) -> None:
    """`python -m tests.fakes.vlm_server --port 8782 --scenario '{"default": "ok"}'` (Playwright e2e)."""
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--scenario", default='{"default": "ok"}', help="JSON text or a path")
    a = ap.parse_args(argv)
    sc = json.loads(a.scenario) if a.scenario.lstrip().startswith("{") else a.scenario
    state = ScenarioState(sc)
    httpd = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(state))
    httpd.daemon_threads = True
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
