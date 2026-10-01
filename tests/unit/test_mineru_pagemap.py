import importlib.util
import json
import re
import sys

from aidoc import paths

sys.path.insert(0, str(paths.runner_dir()))
spec = importlib.util.spec_from_file_location("mineru_runner", paths.runner_script("mineru"))
mr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mr)

MD = "# Title\n\nFirst page text here.\n\nSecond page starts now.\n\n![](images/x.png)\n\nThird.\n"
ITEMS = [{"page_idx": 0, "type": "text", "text": "Title"}, {"page_idx": 0, "type": "text", "text": "First page text here."},
         {"page_idx": 1, "type": "text", "text": "Second page starts now."},
         {"page_idx": 2, "type": "image", "img_path": "images/x.png"}]


def test_markers_inserted():
    out = mr.insert_page_markers(MD, ITEMS)
    assert out.startswith("<!-- page: 1 -->\n# Title") and "<!-- page: 2 -->\nSecond page" in out \
        and "<!-- page: 3 -->\n![](images/x.png)" in out


def test_mapping_failure_returns_none():
    """Most pages cannot be located: the mapping is wrong, so no markers rather than stacked guesses."""
    assert mr.insert_page_markers(MD, [{"page_idx": 0, "type": "text", "text": "Title"},
                                       {"page_idx": 1, "type": "text", "text": "NOT IN MD"},
                                       {"page_idx": 2, "type": "text", "text": "NOR THIS"}]) is None


def test_mineru4_structured_content_shape():
    """MinerU 4.0.7 structured_content.json: pages[].blocks[] with `content` / `image_source` (spike C)."""
    sc = {"pages": [{"page_idx": 0, "blocks": [{"type": "paragraph_title", "content": "Title"}]},
                    {"page_idx": 1, "blocks": [{"type": "text", "content": "Second page starts now."},
                                               {"type": "image", "content": "", "image_source": "images/x.png"}]}]}
    items = mr.flatten_structured(sc)
    out = mr.insert_page_markers(MD, items)
    assert out.startswith("<!-- page: 1 -->\n# Title") and "<!-- page: 2 -->\nSecond page" in out


def test_identical_pages_with_repeated_prefix():
    """scanned_cht.pdf: both pages hold the same paragraph, whose 40-char prefix repeats inside itself."""
    para = "這是一份用來測試文件轉換流程的繁體中文段落。內容包含標題、表格與圖片，用於驗證引擎的中文辨識能力。" * 4
    md = f"{para}\n\n{para}\n"
    items = [{"page_idx": 0, "type": "text", "content": para}, {"page_idx": 1, "type": "text", "content": para}]
    out = mr.insert_page_markers(md, items)
    assert out == f"<!-- page: 1 -->\n{para}\n\n<!-- page: 2 -->\n{para}\n"


def _markers(md):
    return [int(n) for n in re.findall(r"<!-- page: (\d+) -->", md)]


def _page_text(md, page):
    return md.split(f"<!-- page: {page} -->\n", 1)[1].split("<!-- page: ", 1)[0]


def test_recorded_scanned_book_gets_one_marker_per_page(fixtures):
    """Real MinerU 4.0.7 output of a 12-page scanned book (page-number footer, blank page 5): the footer
    `page_number` blocks are not in markdown.md, and their 1-char needle ("1") matched "Chapter 10" far ahead, so
    every later page was unlocatable and the whole segment lost its markers (1192-page book: 30 markers)."""
    base = fixtures / "mineru_recorded" / "book12"
    md = (base / "markdown.md").read_text(encoding="utf-8")
    sc = json.loads((base / "structured_content.json").read_text(encoding="utf-8"))
    out = mr.insert_page_markers(md, mr.flatten_structured(sc), n_pages=12)
    assert out is not None and _markers(out) == list(range(1, 13))
    for k in range(1, 13):
        chapters = re.findall(r"Chapter (\d+):", _page_text(out, k))
        assert chapters == ([] if k == 5 else [str(k)]), (k, chapters)


def test_furniture_blocks_are_not_used_to_locate_pages():
    md = "Chapter 1 text\n\nChapter 2 text\n\nChapter 10 text\n"
    items = [{"page_idx": 0, "type": "text", "content": "Chapter 1 text"},
             {"page_idx": 0, "type": "page_number", "content": "1"},
             {"page_idx": 1, "type": "page_header", "content": "Chapter 10"},
             {"page_idx": 1, "type": "text", "content": "Chapter 2 text"},
             {"page_idx": 2, "type": "text", "content": "Chapter 10 text"}]
    out = mr.insert_page_markers(md, items)
    assert out == ("<!-- page: 1 -->\nChapter 1 text\n\n<!-- page: 2 -->\nChapter 2 text\n\n"
                   "<!-- page: 3 -->\nChapter 10 text\n")


def test_blank_pages_still_get_a_marker():
    """Page sync needs every page: a page with no blocks gets its marker right before the next page's marker."""
    md = "One\n\nThree\n"
    items = [{"page_idx": 0, "type": "text", "content": "One"}, {"page_idx": 2, "type": "text", "content": "Three"}]
    out = mr.insert_page_markers(md, items, n_pages=4)
    assert out == "<!-- page: 1 -->\nOne\n\n<!-- page: 2 -->\n<!-- page: 3 -->\nThree\n<!-- page: 4 -->\n"


def test_image_only_page_without_locatable_content_gets_a_marker():
    md = "One\n\nThree\n"
    items = [{"page_idx": 0, "type": "text", "content": "One"}, {"page_idx": 1, "type": "image", "content": ""},
             {"page_idx": 2, "type": "text", "content": "Three"}]
    assert _markers(mr.insert_page_markers(md, items, n_pages=3)) == [1, 2, 3]


def test_a_single_unlocatable_page_does_not_drop_all_markers():
    """One page whose text differs from the markdown keeps the rest of the segment's markers."""
    md = "Alpha\n\nBravo\n\nDelta\n\nEcho\n"
    items = [{"page_idx": 0, "type": "text", "content": "Alpha"}, {"page_idx": 1, "type": "text", "content": "Bravo"},
             {"page_idx": 2, "type": "text", "content": "Charlie, not in the markdown"},
             {"page_idx": 3, "type": "text", "content": "Delta"}, {"page_idx": 4, "type": "text", "content": "Echo"}]
    out = mr.insert_page_markers(md, items)
    assert _markers(out) == [1, 2, 3, 4, 5]
    assert _page_text(out, 4).strip() == "Delta" and _page_text(out, 2).strip() == "Bravo"
