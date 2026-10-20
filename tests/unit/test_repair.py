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




def test_a_dropped_page_repair_must_cover_its_text_layer():
    """Verifier finding (orthopaedic pages 281/401/911/912/1076): a "repair" holding a heading and images covers
    <= 0.09 of the page's text layer and is not a repair. Accepted only at PARTIAL_COVERAGE_MIN coverage."""
    layer = "thequickbrownfoxjumpsoverthelazydog" * 20
    heading = "## The quick brown fox\n\n![](assets/p1_1.jpg)\n\n![](assets/p1_2.jpg)"
    assert accept_repaired_section(heading, "docling:pypdfium_full_page_ocr", {}, 1)            # no layer: old rule
    assert not accept_repaired_section(heading, "docling:pypdfium_full_page_ocr", {}, 1, layer=layer)
    full = "The quick brown fox jumps over the lazy dog. " * 20
    assert accept_repaired_section(full, "docling:pypdfium_full_page_ocr", {}, 1, layer=layer)


def test_check_document_repairs_dropped_and_partial_pages_against_their_layer(monkeypatch):
    """Dropped / partial pages go to repair with their content layer; a page whose text sits in another section is
    page_misaligned and is NOT repaired (an OCR copy would duplicate the text)."""
    from pathlib import Path
    from types import SimpleNamespace

    from aidoc import pipeline
    from aidoc.models import NormalizedResult, ProbeResult
    from aidoc.pagemap import squash
    seen = {}

    def fake_repair(ctx, engines, norm, pages, main, layers=None):
        seen.update(pages=pages, layers=layers)
        raise RuntimeError("stop")
    monkeypatch.setattr(pipeline, "repair_pages", fake_repair)
    def words(tag):                                          # unique text: probes must be unique in the PDF
        return " ".join(f"{tag}{i}word" for i in range(40))
    p1, p2, p3, p4 = words("alpha"), words("bravo"), words("charlie"), words("delta")
    layers = [squash(x) for x in (p1, p2, p3, p4)]
    monkeypatch.setattr(pipeline.pagemap, "pdf_layers", lambda p: (layers, layers, layers, []))
    probe = ProbeResult(kind="pdf", ext=".pdf", size=1, pages=4)
    ctx = SimpleNamespace(probe=probe, work_src=Path("x.pdf"), engines={"docling": object()}, log=lambda s: None)
    # page 2 dropped, page 3 cut to a few words, page 4's text under page 1 (page 4 empty)
    md = (f"<!-- page: 1 -->\n\n{p1}\n\n{p4}\n\n<!-- page: 2 -->\n\n\n\n<!-- page: 3 -->\n\nquebec romeo sierra "
          f"tango uniform\n\n<!-- page: 4 -->\n\n\n\n")
    try:
        pipeline.check_document(ctx, "docling", NormalizedResult(markdown=md, assets=[]), "fake", last_resort=True)
    except RuntimeError:
        pass
    assert seen["pages"] == [2, 3] and set(seen["layers"]) == {2, 3} and seen["layers"][2] == layers[1]


def test_a_dropped_page_is_repaired_against_its_body_layer_a_partial_one_against_its_prose(monkeypatch):
    """Verifier round 2 (Ortho p779 in the reconversion): a repair accepted against the prose layer was rejected by the
    final check, which judges page_dropped on the body layer. A dropped page's repair must cover its body layer."""
    from pathlib import Path
    from types import SimpleNamespace

    from aidoc import pipeline
    from aidoc.models import NormalizedResult, ProbeResult
    from aidoc.pagemap import squash
    seen = {}

    def fake_repair(ctx, engines, norm, pages, main, layers=None):
        seen.update(pages=pages, layers=layers)
        raise RuntimeError("stop")
    monkeypatch.setattr(pipeline, "repair_pages", fake_repair)

    def words(tag):
        return " ".join(f"{tag}{i}word" for i in range(40))
    full = [squash(words(t)) for t in ("alpha", "bravo", "charlie")]
    body = [f + squash(words("label" + str(i))) for i, f in enumerate(full)]   # figure labels: body only
    monkeypatch.setattr(pipeline.pagemap, "pdf_layers", lambda p: (body, full, body, []))
    probe = ProbeResult(kind="pdf", ext=".pdf", size=1, pages=3)
    ctx = SimpleNamespace(probe=probe, work_src=Path("x.pdf"), engines={"docling": object()}, log=lambda s: None)
    md = (f"<!-- page: 1 -->\n\n{words('alpha')} {words('label0')}\n\n<!-- page: 2 -->\n\n\n\n"
          f"<!-- page: 3 -->\n\n{' '.join(words('charlie').split()[:15])}\n\n")       # a third of page 3
    try:
        pipeline.check_document(ctx, "docling", NormalizedResult(markdown=md, assets=[]), "fake", last_resort=True)
    except RuntimeError:
        pass
    assert seen["pages"] == [2, 3]
    assert seen["layers"][2] == body[1]                 # dropped: body layer
    assert seen["layers"][3] == full[2]                 # partial: prose layer
