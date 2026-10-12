import re
from pathlib import Path

import pymupdf

from aidoc.cli import main
from aidoc.pagemap import MARKER, split_pages
from aidoc.verify import verify_markdown
from aidoc.verify_report import render_html, thumbnails

FIX = Path(__file__).resolve().parents[1] / "fixtures"


def _own(pdf):
    with pymupdf.open(pdf) as d:
        return "".join(f"<!-- page: {i} -->\n\n{p.get_text().strip()}\n\n" for i, p in enumerate(d, 1))


def _no_external(html):
    assert "<script" not in html.lower()
    for m in re.finditer(r'(?:src|href)\s*=\s*["\']([^"\']+)["\']', html):
        assert m.group(1).startswith("data:") or m.group(1).startswith("#"), m.group(1)
    assert "@import" not in html and "url(http" not in html


def test_html_is_self_contained_and_lists_flagged_pages():
    md = _own(FIX / "paged_furniture.pdf")
    _, secs = split_pages(md)
    r = verify_markdown(md.replace(secs[3], "\n\n"), FIX / "paged_furniture.pdf")
    html = render_html(r, thumbnails(FIX / "paged_furniture.pdf", [3]), "cht")
    _no_external(html)
    assert "page_dropped" in html and "data:image/png;base64," in html and "prefers-color-scheme" in html
    assert re.search(r"<tr[^>]*>\s*<td[^>]*>\s*3\s*</td>", html)
    assert "<!doctype html>" in html.lower() and 'lang="zh-Hant"' in html


def test_html_inferred_and_unverifiable_and_escaping(tmp_path):
    md = MARKER.sub("", _own(FIX / "paged_furniture.pdf"))
    html = render_html(verify_markdown(md, FIX / "paged_furniture.pdf", input_name="x<b>&.md"), None, "en")
    assert "inferred" in html and "x&lt;b&gt;&amp;.md" in html and "x<b>" not in html
    _no_external(html)
    html2 = render_html(verify_markdown("無頁碼", FIX / "scanned_cht.pdf"), None, "en")
    assert "unverifiable" in html2.lower()


def test_thumbnails_cap():
    t = thumbnails(FIX / "big.pdf", list(range(1, 46)), width=80, max_pages=10)
    assert len(t) == 10 and all(v[:8] == b"\x89PNG\r\n\x1a\n" for v in t.values())
    assert set(t) == set(range(1, 11))


def test_cli_html_with_thumbnails(tmp_path):
    md = _own(FIX / "paged_furniture.pdf")
    _, secs = split_pages(md)
    p = tmp_path / "a.md"
    p.write_text(md.replace(secs[3], "\n\n"), encoding="utf-8")
    out = tmp_path / "r.html"
    assert main(["verify", str(p), "--pdf", str(FIX / "paged_furniture.pdf"), "--html", str(out), "--thumbnails"]) == 0
    html = out.read_text(encoding="utf-8")
    _no_external(html)
    assert html.count("data:image/png;base64,") == 1
