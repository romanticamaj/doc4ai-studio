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
