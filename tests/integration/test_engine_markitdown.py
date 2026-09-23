import pytest
from aidoc.engines.markitdown import MarkitdownEngine
from aidoc.models import ConvertOptions
from aidoc.probe import probe_file

pytestmark = pytest.mark.slow


@pytest.mark.parametrize("name,expect", [("sample.docx", "R0C0"), ("sample.pptx", "<!-- page: 2 -->"),
                                         ("sample.xlsx", "item1"), ("sample.html", "| a | b |")])
def test_markitdown_converts(tmp_root, fixtures, name, expect):
    e = MarkitdownEngine()
    assert e.available()
    r = e.convert(fixtures / name, tmp_root / "wd", ConvertOptions(output_dir=tmp_root / "out"), None,
                  lambda f, line: None, timeout_s=300, probe=probe_file(fixtures / name))
    assert expect in r.markdown
    if name in ("sample.docx", "sample.pptx"):
        assert len(r.image_paths) == 1 and "images/img_1" in r.markdown
