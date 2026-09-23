import pytest

from aidoc.engines.mineru import MineruEngine
from aidoc.models import ConvertOptions
from aidoc.probe import probe_file

pytestmark = pytest.mark.slow


def conv(tmp_root, fixtures, name, **kw):
    e = MineruEngine()
    assert e.available()
    return e.convert(fixtures / name, tmp_root / "wd", ConvertOptions(output_dir=tmp_root / "out", **kw), None,
                     lambda f, line: None, timeout_s=900, probe=probe_file(fixtures / name))


def test_scanned_cht(tmp_root, fixtures):
    r = conv(tmp_root, fixtures, "scanned_cht.pdf")
    assert "繁體中文" in r.markdown and r.has_page_markers and r.markdown.count("<!-- page: ") == 2
    page1 = r.markdown.split("<!-- page: 1 -->", 1)[1].split("<!-- page: 2 -->", 1)[0]
    page2 = r.markdown.split("<!-- page: 2 -->", 1)[1]
    assert "繁體中文" in page1 and "繁體中文" in page2          # markers sit at real page boundaries


def test_scanned_mixed(tmp_root, fixtures):
    r = conv(tmp_root, fixtures, "scanned_mixed.pdf")
    assert "English" in r.markdown and "中文" in r.markdown


def test_formula(tmp_root, fixtures):
    r = conv(tmp_root, fixtures, "formula.pdf")
    assert "$" in r.markdown


def test_png(tmp_root, fixtures):
    assert "中文" in conv(tmp_root, fixtures, "page.png").markdown
