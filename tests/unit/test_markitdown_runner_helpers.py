import importlib.util
import sys

from aidoc import paths

sys.path.insert(0, str(paths.runner_dir()))
spec = importlib.util.spec_from_file_location("markitdown_runner", paths.runner_script("markitdown"))
mr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mr)


def test_docx_media_in_order(fixtures):
    media = mr.docx_media_in_order(fixtures / "sample.docx")
    assert len(media) == 1 and media[0][0].endswith(".png") and media[0][1][:4] == b"\x89PNG"


def test_replace_nth_images():
    md = "a ![x](data:image/png;base64,AAAA) b ![](data:image/png;base64,BBBB)"
    out = mr.replace_nth_images(md, ["images/i1.png", "images/i2.png"])
    assert out == "a ![x](images/i1.png) b ![](images/i2.png)"


def test_slide_markers():
    assert mr.slide_markers_to_pages("<!-- Slide number: 1 -->\nx\n<!-- Slide number: 2 -->") == \
        "<!-- page: 1 -->\nx\n<!-- page: 2 -->"


def test_split_formfeed_counts_pages():
    assert mr.split_formfeed("a\fb\f\f", 3) == ["a", "b", ""]
    assert mr.split_formfeed("a\fb\f", 3) is None


def test_join_like_markitdown_both_paths():
    assert mr.join_like_markitdown(["A", "", "C"], form_path=True) == "A\n\nC"
    assert mr.join_like_markitdown(["A", "", "C"], form_path=False) == "A\f\fC\f"


def test_md_normalize_matches_markitdown_rules():
    assert mr.md_normalize("a  \r\n\n\n\nb\t \n") == "a\n\nb\n"
