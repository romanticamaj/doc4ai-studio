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


def test_content_filter_and_more_refusal_phrasings_are_refusals():
    assert R.clean_markdown("I'm not able to help with that.") == ("", "refused")
    assert R.clean_markdown("Unfortunately I can't transcribe this image.") == ("", "refused")

    class _C:
        detail = None
        base_url = "http://127.0.0.1:1/v1"

        def chat(self, messages, max_tokens):
            return {"choices": [{"message": {"content": ""}, "finish_reason": "content_filter"}]}
    out = R.send_page(_C(), lambda dpi: (b"\x89PNG", (1, 1), "image/png"), 1, 1, {"prompt_style": "generic"}, {})
    assert out.flag == "refused"


def test_one_unreachable_page_is_a_page_failure_not_a_lost_run(tmp_path, monkeypatch):
    monkeypatch.setenv("AIDOC_TEST_VLM_KEY", "k")
    monkeypatch.setattr(R, "_sleep", lambda s: None)
    sc = {"default": "ok", "rules": [{"match": {"page": 2}, "behavior": "server_error"}]}
    with FakeVlmServer(sc) as base:
        res = R.handle(_req(tmp_path, base, max_concurrent=1))
    assert res["failed_pages"] == {"2": "unreachable"} and res["vlm"]["pages_sent"] == 3
    with FakeVlmServer({"default": "server_error"}) as base, pytest.raises(R._proto.RunnerError) as e:
        R.handle(_req(tmp_path, base))
    assert e.value.kind == "transient"


def test_loopback_is_an_address_not_a_prefix():
    from aidoc.engines.vlm import is_loopback_url
    assert is_loopback_url("http://127.0.0.1:1/v1") and is_loopback_url("http://[::1]:1/v1") and is_loopback_url("http://localhost/v1")
    assert not is_loopback_url("http://127.evil.com/v1") and not R._is_loopback("127.evil.com:80")


LOOPBACK_TABLE = Path(__file__).resolve().parents[2] / "web" / "src" / "features" / "settings" / "__tests__" / "loopbackCases.json"


def test_loopback_rules_are_one_table_for_server_and_web():
    from aidoc.engines.runner import _vlm_net
    from aidoc.engines.vlm import is_loopback_url
    for url, expected in json.loads(LOOPBACK_TABLE.read_text(encoding="utf-8"))["cases"]:
        assert _vlm_net.is_loopback_url(url) is (bool(expected) if expected is not None else False), url
        assert is_loopback_url(url) is (bool(expected) if expected is not None else False), url


REFUSAL_TABLE = [
    # (answer, is a refusal) — one table for both directions (verifier follow-up 1)
    ("I'm sorry, I can't help with that.", True),
    ("抱歉，我無法辨識這張圖片。", True),
    ("I'm not able to help with that.", True),
    ("Unfortunately I can't transcribe this image.", True),
    ("很抱歉，我無法協助處理這個請求。", True),
    ("I'm sorry, I can't do that.", True),
    ("I cannot comply.", True),
    ("As an AI language model, I cannot do this.", True),
    ("無法處理。", True),
    ("很抱歉，我不能提供這個內容。", True),
    ("無法忘記的那個夏天，我們在海邊。", False),
    ("抱歉，我遲到了。她說。", False),
    ("Unfortunately, the war began in 1914.", False),
    ("I cannot tell a lie, said George.", False),
    ("Sorry, we're closed", False),
]


@pytest.mark.parametrize("text,refused", REFUSAL_TABLE)
def test_refusal_table(text, refused):
    assert R.clean_markdown(text) == (("", "refused") if refused else (text, None))


@pytest.mark.parametrize("text", ["無法忘記的那個夏天，我們在海邊。", "抱歉，我遲到了。她說。", "Unfortunately, the war began in 1914.",
                                  "I cannot tell a lie, said George.", "Sorry, we're closed"])
def test_ordinary_sentences_are_not_refusals(text):
    assert R.clean_markdown(text) == (text, None)


@pytest.mark.parametrize("text", ["I'm sorry, I can't help with that.", "抱歉，我無法辨識這張圖片。", "I'm not able to help with that.",
                                  "Unfortunately I can't transcribe this image.", "很抱歉，我無法協助處理這個請求。"])
def test_refusals_name_the_task(text):
    assert R.clean_markdown(text) == ("", "refused")


def test_errors_never_carry_the_key_or_document_text(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AIDOC_TEST_VLM_KEY", "sk-live-SECRET123456")
    with FakeVlmServer({"default": "unauthorized_echo"}) as base, pytest.raises(R._proto.RunnerError) as e:
        R.handle(_req(tmp_path, base))
    err = capsys.readouterr().err
    assert "sk-live-SECRET123456" not in str(e.value) + err and "Bearer sk" not in str(e.value) + err
    assert "***" in str(e.value)
    monkeypatch.setattr(R, "_sleep", lambda s: None)
    with FakeVlmServer({"default": "malformed"}) as base, pytest.raises(R._proto.RunnerError) as e:
        R.handle(_req(tmp_path, base, max_concurrent=1))
    assert "CONFIDENTIAL PAGE TEXT" not in str(e.value) + capsys.readouterr().err


def test_every_request_is_reported_as_it_leaves(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AIDOC_TEST_VLM_KEY", "k")
    monkeypatch.setattr(R, "_sleep", lambda s: None)
    sc = {"default": "ok", "rules": [{"match": {"page": 2, "request_no": 1}, "behavior": "server_error"}],
          "call_log": str(tmp_path / "c.jsonl")}
    with FakeVlmServer(sc) as base:
        R.handle(_req(tmp_path, base, max_concurrent=1))
    events = [json.loads(line.split(" ", 1)[1]) for line in capsys.readouterr().err.splitlines()
              if line.startswith("AIDOC_VLM_USAGE ")]
    calls = (tmp_path / "c.jsonl").read_text(encoding="utf-8").splitlines()
    assert sum(ev.get("requests", 0) for ev in events) == len(calls) == 4
    assert sum(ev.get("pages", 0) for ev in events) == 3 and all(ev["endpoint_host"].startswith("127.0.0.1:") for ev in events)


def test_answered_pages_are_not_sent_again_on_a_retry(tmp_path, monkeypatch):
    monkeypatch.setenv("AIDOC_TEST_VLM_KEY", "k")
    sc = {"default": "ok", "call_log": str(tmp_path / "c.jsonl")}
    with FakeVlmServer(sc) as base:
        first = R.handle(_req(tmp_path, base))
        second = R.handle(_req(tmp_path, base))
        other_model = R.handle(_req(tmp_path, base, model="other"))
    assert len((tmp_path / "c.jsonl").read_text(encoding="utf-8").splitlines()) == 6     # 3 + 0 + 3
    assert Path(first["markdown_path"]).read_text(encoding="utf-8") == Path(second["markdown_path"]).read_text(encoding="utf-8")
    assert second["vlm"]["pages_sent"] == 0 and other_model["vlm"]["pages_sent"] == 3


def test_request_counter_is_exact_under_concurrency(tmp_path, monkeypatch):
    monkeypatch.setenv("AIDOC_TEST_VLM_KEY", "k")
    sc = {"default": "ok", "call_log": str(tmp_path / "c.jsonl")}
    with FakeVlmServer(sc) as base:
        res = R.handle(_req(tmp_path, base, src=FIX / "big.pdf", max_concurrent=8, max_image_px=600))
    assert res["vlm"]["usage"]["requests"] == len((tmp_path / "c.jsonl").read_text(encoding="utf-8").splitlines()) == 45


def test_page_cache_key_is_the_whole_endpoint_not_just_host_port(tmp_path):
    req = {"src": str(FIX / "text.pdf"), "out_dir": str(tmp_path / "raw")}
    eo = {"model": "m", "dpi": 150}
    keys = {R._cache_dir(req, {**eo, "base_url": u}, "h:1", 3)
            for u in ("http://h:1/v1", "http://h:1/v2", "https://h:1/v1", "http://h:2/v1")}
    assert len(keys) == 4
    assert R._cache_dir(req, {**eo, "base_url": "http://h:1/v1/"}, "h:1", 3) == R._cache_dir(req, {**eo, "base_url": "http://H:1/v1"}, "h:1", 3)


def test_a_loopback_endpoint_never_goes_through_a_proxy(monkeypatch):
    """Privacy (verifier finding 2026-10-22): urllib follows HTTP_PROXY even for 127.0.0.1; pages sent to a local
    VLM must not reach a proxy."""
    from tests.fakes.proxy import RecordingProxy, point_env_at
    vr = R
    with RecordingProxy() as proxy, FakeVlmServer({"default": "ok"}) as url:
        point_env_at(monkeypatch, proxy)
        c = vr.Client(url, "m", None, 10, None, 256)
        out = c.chat([{"role": "user", "content": "hi"}], 16)
        assert out["choices"] and proxy.hits == []
