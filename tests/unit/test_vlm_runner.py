import json
from pathlib import Path

import pytest

from aidoc.engines.runner import _vlm_prompt as P
from aidoc.engines.runner import vlm_runner as R
from tests.fakes.vlm_server import FakeVlmServer

FIX = Path(__file__).resolve().parents[1] / "fixtures"


def test_prompt_version_is_pinned_to_text():
    # changing any prompt text must bump PROMPT_VERSION and this constant
    assert P.PROMPT_VERSION == 1
    assert P.prompt_sha256() == P.PINNED_SHA256


def test_messages_shapes():
    m = P.messages("generic", "cht", "data:image/png;base64,AAAA", 2, 9, detail="high")
    assert m[0]["role"] == "system" and "Traditional Chinese" in m[0]["content"]
    parts = m[1]["content"]
    assert parts[0]["type"] == "image_url" and parts[0]["image_url"]["detail"] == "high"
    assert parts[1]["text"].startswith("Page 2 of 9")
    t = P.messages("paddle_tasks", "cht", "data:image/png;base64,AAAA", 1, 1, detail=None)
    assert len(t) == 1 and t[0]["content"][1]["text"] == "OCR:" and "detail" not in t[0]["content"][0]["image_url"]


def test_render_page_respects_max_px():
    import pymupdf
    with pymupdf.open(FIX / "text.pdf") as doc:
        png, (w, h), mime = R.render_page(doc, 0, dpi=200, max_px=900)
    assert mime == "image/png" and max(w, h) <= 900 and png[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.parametrize("text,expect", [
    ("```markdown\n# T\n\nbody\n```", ("# T\n\nbody", None)),
    ("# T\n\nbody\n\nLet me know if you need anything else!", ("# T\n\nbody", None)),
    ("<!-- blank -->", ("", "blank")),
    ("I'm sorry, I can't help with that.", ("", "refused")),
    ("抱歉，我無法辨識這張圖片。", ("", "refused")),
])
def test_clean_markdown(text, expect):
    assert R.clean_markdown(text) == expect


def _opts(base, **kw):
    return {"backend": "openai", "base_url": base, "model": "fake-vlm", "api_key_env": "AIDOC_TEST_VLM_KEY",
            "dpi": 100, "max_image_px": 800, "request_timeout_s": 5, "max_concurrent": 2, "max_output_tokens": 256,
            "detail": "high", "prompt_version": 1, "prompt_style": "generic", "lang": "cht", **kw}


def _req(tmp_path, base, src=FIX / "text.pdf", **kw):
    return {"src": str(src), "out_dir": str(tmp_path / "raw"), "lang": "cht", "kind": "pdf", "pages": None,
            "segment_idx": 0, "engine_opts": _opts(base, **kw)}


def test_handle_marks_every_page_and_reports_usage(tmp_path, monkeypatch):
    monkeypatch.setenv("AIDOC_TEST_VLM_KEY", "sk-test")
    sc = {"default": "ok", "require_key": "sk-test", "pages": {"2": "第二頁 second page"}, "call_log": str(tmp_path / "calls.jsonl")}
    with FakeVlmServer(sc) as base:
        res = R.handle(_req(tmp_path, base))
    md = Path(res["markdown_path"]).read_text(encoding="utf-8")
    assert md.count("<!-- page: ") == 3 and "第二頁 second page" in md
    assert res["has_page_markers"] and res["page_map_method"] == "vlm_per_page" and res["page_count"] == 3
    v = res["vlm"]
    assert v["backend"] == "openai" and v["pages_sent"] == 3 and v["usage"]["requests"] == 3 and v["usage"]["prompt_tokens"] > 0
    assert v["endpoint_host"].startswith("127.0.0.1:") and v["prompt_version"] == 1
    calls = [json.loads(l) for l in (tmp_path / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    assert all(c["has_auth"] for c in calls) and all(max(c["image_px"]) <= 800 for c in calls)
    assert "sk-test" not in json.dumps(res)                      # the key never reaches result.json


def test_refusal_truncation_and_blank(tmp_path, monkeypatch):
    monkeypatch.setenv("AIDOC_TEST_VLM_KEY", "k")
    sc = {"default": "ok", "rules": [{"match": {"page": 1}, "behavior": "refuse"},
                                     {"match": {"page": 2}, "behavior": "length"},
                                     {"match": {"page": 3}, "behavior": "blank"}]}
    with FakeVlmServer(sc) as base:
        res = R.handle(_req(tmp_path, base))
    assert res["failed_pages"] == {"1": "refused", "2": "truncated"}
    assert res["vlm"]["refused_pages"] == [1] and res["vlm"]["truncated_pages"] == [2]
    md = Path(res["markdown_path"]).read_text(encoding="utf-8")
    assert md.count("<!-- page: ") == 3                          # the blank page keeps its marker, empty section


def test_retry_schedule_and_retry_after(tmp_path, monkeypatch):
    monkeypatch.setenv("AIDOC_TEST_VLM_KEY", "k")
    slept = []
    monkeypatch.setattr(R, "_sleep", lambda s: slept.append(s))
    sc = {"default": "ok", "rules": [{"match": {"page": 1, "request_no": 1}, "behavior": "rate_limit"},
                                     {"match": {"page": 1, "request_no": 2}, "behavior": "server_error"}]}
    with FakeVlmServer(sc) as base:
        res = R.handle(_req(tmp_path, base, max_concurrent=1))
    assert res["failed_pages"] == {} and slept[0] == 1 and slept[1] == R.REMOTE_RETRY_BACKOFF[1]


def test_auth_error_is_engine_error_not_retry(tmp_path, monkeypatch):
    monkeypatch.setenv("AIDOC_TEST_VLM_KEY", "wrong")
    with FakeVlmServer({"default": "ok", "require_key": "right"}) as base, pytest.raises(R._proto.RunnerError) as e:
        R.handle(_req(tmp_path, base))
    assert e.value.kind == "engine" and "401" in str(e.value)


def test_missing_key_is_engine_error(tmp_path, monkeypatch):
    monkeypatch.delenv("AIDOC_TEST_VLM_KEY", raising=False)
    monkeypatch.delenv("AIDOC_VLM_API_KEY_INLINE", raising=False)
    with FakeVlmServer({"default": "ok"}) as base:
        with pytest.raises(R._proto.RunnerError, match="API key"):
            R.handle(_req(tmp_path, base))
        assert R.handle(_req(tmp_path, base, allow_no_key=True))["page_count"] == 3


def test_bad_request_rerenders_at_lower_dpi(tmp_path, monkeypatch):
    monkeypatch.setenv("AIDOC_TEST_VLM_KEY", "k")
    sc = {"default": "ok", "rules": [{"match": {"page": 1, "request_no": 1}, "behavior": "bad_request"}], "call_log": str(tmp_path / "c.jsonl")}
    with FakeVlmServer(sc) as base:
        res = R.handle(_req(tmp_path, base, dpi=150))
    calls = [json.loads(l) for l in (tmp_path / "c.jsonl").read_text(encoding="utf-8").splitlines() if '"page": 1' in l]
    assert res["failed_pages"] == {} and max(calls[1]["image_px"]) < max(calls[0]["image_px"])


def test_otsl_table_becomes_gfm():
    otsl = "<fcel>R0C0 值<fcel>R0C1 值<nl><fcel>R1C0<lcel><nl><fcel>a|b<ecel><nl>"
    assert R.otsl_to_gfm(otsl) == "| R0C0 值 | R0C1 值 |\n| --- | --- |\n| R1C0 | R1C0 |\n| a\\|b |  |"
    assert R.otsl_to_gfm("plain text, no table") is None


def test_latex_delimiters_and_missing_images():
    md, flag = R.clean_markdown("Intro\n\n" r"\(E = mc^2\)" "\n\n" r"\[\int_0^1 x\,dx\]")
    assert flag is None and "$E = mc^2$" in md and r"$$\int_0^1 x\,dx$$" in md
    assert R.mark_missing_images("a\n<!-- image -->\nFig. 1\n<!-- image -->") == (
        "a\n<!-- image missing -->\nFig. 1\n<!-- image missing -->", 2)


def test_images_and_failed_pages_in_result(tmp_path, monkeypatch):
    monkeypatch.setenv("AIDOC_TEST_VLM_KEY", "k")
    sc = {"default": "ok", "pages": {"1": "text\n\n<!-- image -->\nFigure 1. caption"},
          "rules": [{"match": {"page": 2}, "behavior": "bad_request"}]}
    with FakeVlmServer(sc) as base:
        res = R.handle(_req(tmp_path, base, dpi=100))
    assert res["images_missing"] == {"1": 1} and res["failed_pages"] == {"2": "bad_request"}
    assert "<!-- image missing -->" in Path(res["markdown_path"]).read_text(encoding="utf-8")


def test_no_usage_endpoint_reports_null_tokens(tmp_path, monkeypatch):
    monkeypatch.setenv("AIDOC_TEST_VLM_KEY", "k")
    with FakeVlmServer({"default": "no_usage"}) as base:
        res = R.handle(_req(tmp_path, base))
    assert res["vlm"]["usage"] == {"requests": 3, "prompt_tokens": None, "completion_tokens": None}
