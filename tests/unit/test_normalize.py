from pathlib import Path
from aidoc.models import RawResult
from aidoc.normalize import normalize, html_table_to_gfm


def raw(md, images=(), markers=True, tmp=Path(".")):
    return RawResult(markdown=md, image_paths=list(images), raw_dir=tmp, has_page_markers=markers)


def test_page_offset():
    r = normalize(raw("<!-- page: 1 -->\na\n<!-- page: 2 -->\nb"), page_offset=40, seg_idx=1)
    assert "<!-- page: 41 -->" in r.markdown and "<!-- page: 42 -->" in r.markdown


def test_images_renamed_by_page(tmp_path):
    (tmp_path / "images").mkdir()
    a = tmp_path / "images" / "page_0_img_1.png"
    a.write_bytes(b"x")
    b = tmp_path / "images" / "img2.jpeg"
    b.write_bytes(b"y")
    md = ("<!-- page: 1 -->\n![](images/page_0_img_1.png)\n<!-- page: 2 -->\n![alt](images/img2.jpeg)\n"
          "![](images/img2.jpeg)")
    r = normalize(raw(md, [a, b], tmp=tmp_path), 0, 0)
    assert r.assets == [(a, "p1_1.png"), (b, "p2_1.jpeg")]
    assert "![](assets/p1_1.png)" in r.markdown and r.markdown.count("assets/p2_1.jpeg") == 2


def test_images_without_page_markers(tmp_path):
    a = tmp_path / "x.png"
    a.write_bytes(b"x")
    r = normalize(raw("![](x.png)", [a], markers=False, tmp=tmp_path), 80, 2)
    assert r.assets == [(a, "s2_1.png")] and "assets/s2_1.png" in r.markdown


def test_html_table_converted():
    html = "<table><tr><th>a</th><th>b</th></tr><tr><td>1</td><td>2 中</td></tr></table>"
    assert html_table_to_gfm(html) == "| a | b |\n| --- | --- |\n| 1 | 2 中 |"
    r = normalize(raw(f"text\n{html}\nmore"), 0, 0)
    assert "| a | b |" in r.markdown and "<table" not in r.markdown


def test_html_table_with_span_kept():
    html = '<table><tr><td colspan="2">x</td></tr><tr><td>1</td><td>2</td></tr></table>'
    assert html_table_to_gfm(html) is None
    assert "<table" in normalize(raw(html), 0, 0).markdown


def test_whitespace_normalised():
    r = normalize(raw("a  \r\nb\n\n\n\n\nc"), 0, 0)
    assert r.markdown == "a\nb\n\nc\n"


def test_formulas_untouched():
    md = "$x^2$ and $$\\int_0^1 f$$"
    assert md in normalize(raw(md), 0, 0).markdown
