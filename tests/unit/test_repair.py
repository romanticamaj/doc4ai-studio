from aidoc.repair import repair_order


def test_repair_order_prefers_same_style_engine():
    both = {"docling": True, "mineru": True}
    assert [lbl for _, _, lbl in repair_order("docling", both)] == ["docling:pypdfium_full_page_ocr", "mineru:ocr"]
    assert [lbl for _, _, lbl in repair_order("markitdown", both)][0] == "docling:pypdfium_full_page_ocr"
    assert [lbl for _, _, lbl in repair_order("mineru", both)] == ["mineru:ocr", "docling:pypdfium_full_page_ocr"]
    assert [e for e, _, _ in repair_order("docling", {"docling": False, "mineru": True})] == ["mineru"]


from aidoc.repair import accept_repaired_section


class _E:  # minimal stand-in with a label
    def __init__(self, lbl): self._l = lbl
    def label(self): return self._l


AV = {"docling": True, "mineru": True, "vlm": True}
ENG = {"vlm": _E("vlm:openai:m")}


def test_repair_order_appends_vlm_last():
    assert [o[0] for o in repair_order("docling", AV, engines=ENG)] == ["docling", "mineru", "vlm"]
    assert [o[0] for o in repair_order("mineru", AV, engines=ENG)] == ["mineru", "docling", "vlm"]
    assert repair_order("docling", AV, engines=ENG)[-1][2] == "vlm:openai:m"
    assert repair_order("vlm", AV, engines=ENG) == []
    assert [o[0] for o in repair_order("docling", AV, engines=ENG, allow_vlm=False)] == ["docling", "mineru"]
    assert [o[0] for o in repair_order("docling", {**AV, "vlm": False}, engines=ENG)] == ["docling", "mineru"]


def test_vlm_repairs_reject_refused_truncated_and_blank():
    good = "Wouldn't it be dreamy if there was a book on Android that was easy to read. " * 3
    assert accept_repaired_section(good, "vlm:openai:m", {}, 1)
    assert not accept_repaired_section(good, "vlm:openai:m", {1: "truncated"}, 1)
    assert not accept_repaired_section("<!-- blank -->", "vlm:openai:m", {}, 1)
    assert not accept_repaired_section("", "docling:pypdfium_full_page_ocr", {}, 1)
    assert accept_repaired_section(good, "docling:pypdfium_full_page_ocr", {1: "x"}, 1)   # only vlm looks at failed


def test_repair_only_test_hook_keeps_the_named_engines(monkeypatch):
    monkeypatch.setenv("AIDOC_REPAIR_ONLY", "vlm")
    assert [o[0] for o in repair_order("docling", AV, engines=ENG)] == ["vlm"]
    monkeypatch.setenv("AIDOC_REPAIR_ONLY", "mineru, vlm")
    assert [o[0] for o in repair_order("docling", AV, engines=ENG)] == ["mineru", "vlm"]


def test_vlm_repair_gets_the_remote_timeout(tmp_path, fixtures):
    """verifier finding 2: repair uses engine.timeout_factor like the main conversion."""
    from types import SimpleNamespace

    from aidoc.config import AidocConfig
    from aidoc.models import ConvertOptions, ProbeResult
    from aidoc.repair import repair_pages
    seen = {}

    class _Sess:
        pid = 1

        def convert(self, src, wd, pages, cb, *, timeout_s, segment_idx, cancel):
            seen["timeout"] = timeout_s
            raise RuntimeError("stop here")

        def close(self):
            pass

    class _Vlm(_E):
        def available(self): return True
        def is_remote(self): return False
        def engine_opts(self, opts, probe): return {}
        def open_session(self, opts, probe, engine_opts): return _Sess()
        def timeout_factor(self, pages, timeout): return 4321.0

    ctx = SimpleNamespace(work_dir=tmp_path, work_src=fixtures / "text.pdf", opts=ConvertOptions(output_dir=tmp_path),
                          probe=ProbeResult(kind="pdf", ext=".pdf", size=1, pages=3), config=AidocConfig(), cancel=None,
                          cancelled=lambda: False, log=lambda s: None)
    norm = SimpleNamespace(markdown="<!-- page: 1 -->\nx", assets=[])
    repair_pages(ctx, {"vlm": _Vlm("vlm:local:x")}, norm, [1], "docling")
    assert seen["timeout"] == 4321.0


def test_a_dropped_page_needs_a_real_section_to_count_as_repaired():
    """Benchmark finding (orthopaedic book page 912): a page_dropped page "repaired" with a few characters is still
    dropped; it must stay unrepaired (min_chars), while other pages keep the plain has-text rule."""
    assert accept_repaired_section("Fig. 3", "docling:pypdfium_full_page_ocr", {}, 1)
    assert not accept_repaired_section("Fig. 3", "docling:pypdfium_full_page_ocr", {}, 1, min_chars=20)
    assert accept_repaired_section("x" * 25, "docling:pypdfium_full_page_ocr", {}, 1, min_chars=20)


def test_check_document_asks_dropped_pages_for_a_real_section(monkeypatch):
    from aidoc import pipeline
    from aidoc.quality import DROPPED_SECTION_MAX
    seen = {}

    def fake_repair(ctx, engines, norm, pages, main, min_chars=None):
        seen.update(pages=pages, min_chars=min_chars)
        raise RuntimeError("stop")
    monkeypatch.setattr(pipeline, "repair_pages", fake_repair)
    monkeypatch.setattr(pipeline.pagemap, "pdf_text_layers", lambda p: ["x" * 200] * 3)
    from pathlib import Path
    from types import SimpleNamespace

    from aidoc.models import NormalizedResult, ProbeResult
    probe = ProbeResult(kind="pdf", ext=".pdf", size=1, pages=3)
    ctx = SimpleNamespace(probe=probe, work_src=Path("x.pdf"), engines={"docling": object()}, log=lambda s: None)
    md = "<!-- page: 1 -->\n\n" + "a" * 50 + "\n\n<!-- page: 2 -->\n\n\n\n<!-- page: 3 -->\n\n" + "b" * 50
    try:
        pipeline.check_document(ctx, "docling", NormalizedResult(markdown=md, assets=[]), "fake", last_resort=True)
    except RuntimeError:
        pass
    assert seen == {"pages": [2], "min_chars": {2: DROPPED_SECTION_MAX}}
