from aidoc.models import ProbeResult
from aidoc.quality import assess, has_table


def pdf(pages=2, **kw):
    return ProbeResult(kind="pdf", ext=".pdf", size=1, pages=pages, **kw)


GOOD = "\n".join(["<!-- page: 1 -->", "# 標題", "這是一段夠長的中文內容。" * 10, "<!-- page: 2 -->",
                  "More English text here. " * 10])


def test_ok():
    q = assess(GOOD, pdf(), 2)
    assert q.level == "ok" and q.reasons == [] and 0.9 <= q.score <= 1.0
    assert q.metrics["chars_per_page"] > 50


def test_too_few_chars():
    q = assess("<!-- page: 1 -->\nhi\n<!-- page: 2 -->\nyo", pdf(), 2)
    assert q.level == "low" and "chars_per_page" in q.reasons


def test_blank_pages_excluded():
    md = "<!-- page: 1 -->\n" + "x" * 80 + "\n<!-- page: 2 -->\n"
    assert assess(md, pdf(pages=2, blank_pages=[2]), 2).level == "ok"


def test_zero_effective_pages_no_crash():
    q = assess("", pdf(pages=1, blank_pages=[1]), 1)
    assert q.level == "low" and "chars_per_page" in q.reasons


def test_garbage():
    md = "<!-- page: 1 -->\n" + ("�" * 10 + "abcdefghij" * 10)
    q = assess(md, pdf(pages=1), 1)
    assert "garbage_ratio" in q.reasons
    md2 = "<!-- page: 1 -->\n" + "" * 30 + "a" * 100
    assert "garbage_ratio" in assess(md2, pdf(pages=1), 1).reasons


def test_missing_table():
    q = assess(GOOD, pdf(has_table_lines=True), 2)
    assert "missing_table" in q.reasons and q.level == "low"
    assert assess(GOOD + "\n| a | b |\n|---|---|\n| 1 | 2 |\n", pdf(has_table_lines=True), 2).level == "ok"
    assert assess(GOOD + "\n<table><tr><td>1</td></tr></table>", pdf(has_table_lines=True), 2).level == "ok"


def test_non_pdf_whole_doc_threshold():
    off = ProbeResult(kind="office", ext=".docx", size=1)
    assert assess("short", off, None).level == "low"
    assert assess("x" * 60, off, None).level == "ok"


def test_markup_not_counted():
    md = "<!-- page: 1 -->\n![img](assets/p1_1.png)\n" + "|---|" * 20
    assert assess(md, pdf(pages=1), 1).metrics["chars"] < 50


def test_has_table():
    assert has_table("| a |\n|---|\n| 1 |") and has_table("<TABLE>") and not has_table("| just a pipe")


def test_meaningful_symbols_are_not_garbage():
    from aidoc.quality import garbage_ratio
    for s in ["評價 ★★★★☆ 很好", "流程 →→→→ 下一步", "重點 ●●●● 項目", "目錄…………………12", "Straße ÄÖÜß façade",
              "價格 ¥1,200 · 溫度 25°C ± 2", "━━━━ 分隔線 ━━━━", "①②③④ 步驟"]:
        assert garbage_ratio(s) == 0.0, s


def test_broken_sequences_are_garbage():
    from aidoc.quality import garbage_ratio
    assert garbage_ratio("ä¸­æ–‡ä¸­æ–‡ å ±å‘Š") > 0.05                  # UTF-8 read as cp1252 (mojibake)
    assert garbage_ratio("abc���") > 0.05
    assert garbage_ratio("abc") > 0.05                  # private use
    assert garbage_ratio("abc\x01\x02\x03\x85") > 0.05                    # C0 / C1 controls
    assert garbage_ratio("abc͸͹΀") > 0.05                  # unassigned code points


from aidoc.quality import flag_page, flag_pages, page_signals

R = "\N{REPLACEMENT CHARACTER}"
GOOD_EN = "This is an ordinary English paragraph with plenty of normal words for the quality gate. " * 4
# T-001 cover as Docling printed it (glyph ids, +29 shift, glyph-3 spaces -> U+FFFD)
GARBLED = f":RXOGQ·W{R}LW{R}EH{R}GUHDP\\{R}LI{R}WKHUH{R}ZDV{R}D{R}ERRN{R}RQ{R}$QGURLG"


def _probe(pages, broken=()):
    return ProbeResult(kind="pdf", ext=".pdf", size=1, pages=pages, broken_font_pages=list(broken))


def _doc(sections):
    return "".join(f"<!-- page: {i} -->\n\n{s}\n\n" for i, s in enumerate(sections, 1))


def test_page_signals_on_t001_cover():
    s = page_signals(GARBLED)
    assert s["fffd_between"] == 10 and s["shift_hits"] >= 3      # it, be, if, there, was, on
    g = page_signals(GOOD_EN)
    assert g["fffd_between"] == 0 and g["shift_hits"] == 0


def test_flag_page_requires_font_gate_for_broken_text_layer():
    s = page_signals(GOOD_EN + " W" + R + "X")
    assert flag_page(s, broken_font=True) == ["broken_text_layer"]
    assert flag_page(s, broken_font=False) == []                      # one U+FFFD alone is not enough ungated


def test_flag_pages_uses_markers():
    md = _doc([GOOD_EN, GOOD_EN + GARBLED, GOOD_EN])
    assert [f["page"] for f in flag_pages(md, broken_font_pages=[2, 3])] == [2]


def test_missing_markers_make_pdf_low():
    q = assess("\n\n".join([GOOD_EN] * 3), _probe(3), 3, page_map_method="none")
    assert q.level == "low" and "page_map_incomplete" in q.reasons and q.page_map["coverage"] == 0.0


def test_partial_markers_warn_between_95_and_100_percent():
    md = _doc([GOOD_EN] * 40).replace("<!-- page: 17 -->", "")
    q = assess(md, _probe(40), 40, page_map_method="docling_per_page")
    assert q.level == "warn" and q.page_map["missing"] == [17]
    assert {"page": 17, "reasons": ["page_map_missing"]}.items() <= q.pages[0].items()


def test_unrepaired_broken_page_warns_and_repaired_page_is_ok():
    md = _doc([GOOD_EN, GOOD_EN + GARBLED] + [GOOD_EN] * 8)
    q = assess(md, _probe(10, broken=[2]), 10, page_map_method="docling_per_page")
    assert q.level == "warn" and q.pages[0]["reasons"] == ["broken_text_layer"]
    entry = {**q.pages[0], "repaired_by": "docling:pypdfium_full_page_ocr"}
    q2 = assess(_doc([GOOD_EN] * 10), _probe(10, broken=[2]), 10, page_map_method="docling_per_page",
                flagged_before=1, repaired=[entry])
    assert q2.level == "ok" and q2.pages_flagged == 1 and q2.pages == [entry]
    assert q2.to_json()["pages_unrepaired"] == 0


def test_too_many_flagged_pages_is_low_but_not_in_quick_check():
    md = _doc([GOOD_EN + GARBLED] * 3 + [GOOD_EN] * 7)
    probe = _probe(10, broken=[1, 2, 3])
    assert "pages_flagged" in assess(md, probe, 10, page_map_method="x").reasons
    assert "pages_flagged" not in assess(md, probe, 10, page_map_method="x", quick=True).reasons


def test_non_pdf_keeps_legacy_rules():
    q = assess(GOOD_EN, ProbeResult(kind="office", ext=".docx", size=1), None)
    assert q.level == "ok" and q.page_map is None and q.page_check == 1
