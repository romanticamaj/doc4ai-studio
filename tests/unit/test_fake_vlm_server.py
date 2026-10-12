import base64
import json
import struct
import urllib.request
import zlib

from tests.fakes.vlm_server import FakeVlmServer


def _png(w=40, h=30):
    raw = b"".join(b"\x00" + b"\xff" * (w * 3) for _ in range(h))
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _post(base, body, key=None):
    req = urllib.request.Request(base + "/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {key}"} if key else {})})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read()), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}"), dict(e.headers)


def _body(page=3, total=10, model="fake-vlm"):
    uri = "data:image/png;base64," + base64.b64encode(_png()).decode()
    return {"model": model, "max_tokens": 100, "messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": uri, "detail": "high"}},
        {"type": "text", "text": f"Page {page} of {total}. Return the Markdown of this page."}]}]}


def test_ok_uses_page_text_and_logs_image_size(tmp_path):
    sc = {"default": "ok", "pages": {"3": "第三頁的內容"}, "call_log": str(tmp_path / "calls.jsonl")}
    with FakeVlmServer(sc) as base:
        status, out, _ = _post(base, _body())
    assert status == 200 and "第三頁的內容" in out["choices"][0]["message"]["content"]
    assert out["choices"][0]["finish_reason"] == "stop" and out["usage"]["prompt_tokens"] > 0
    call = json.loads((tmp_path / "calls.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert call["page"] == 3 and call["total"] == 10 and call["image_px"] == [40, 30] and call["has_auth"] is False


def test_rules_by_page_and_request_no(tmp_path):
    sc = {"default": "ok", "rules": [{"match": {"page": 3, "request_no": 1}, "behavior": "rate_limit"},
                                     {"match": {"page": 4}, "behavior": "length"}]}
    with FakeVlmServer(sc) as base:
        s1, _, h1 = _post(base, _body(3)); s2, _, _ = _post(base, _body(3)); s3, o3, _ = _post(base, _body(4))
    assert (s1, s2, s3) == (429, 200, 200) and h1.get("Retry-After") == "1"
    assert o3["choices"][0]["finish_reason"] == "length"


def test_require_key_and_errors(tmp_path):
    with FakeVlmServer({"default": "ok", "require_key": "sk-test"}) as base:
        assert _post(base, _body())[0] == 401
        assert _post(base, _body(), key="sk-test")[0] == 200
    with FakeVlmServer({"default": "model_missing"}) as base:
        status, out, _ = _post(base, _body())
    assert status == 404 and out["error"]["code"] == "model_not_found"


def test_models_endpoint():
    with FakeVlmServer({"default": "ok", "model": "fake-vlm"}) as base,             urllib.request.urlopen(base + "/models", timeout=5) as r:
        assert json.loads(r.read())["data"][0]["id"] == "fake-vlm"
