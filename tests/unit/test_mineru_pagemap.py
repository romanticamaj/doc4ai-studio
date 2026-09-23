import importlib.util
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
    assert mr.insert_page_markers(MD, [{"page_idx": 0, "type": "text", "text": "Title"},
                                       {"page_idx": 1, "type": "text", "text": "NOT IN MD"}]) is None


def test_mineru4_structured_content_shape():
    """MinerU 4.0.7 structured_content.json: pages[].blocks[] with `content` / `image_source` (spike C)."""
    sc = {"pages": [{"page_idx": 0, "blocks": [{"type": "paragraph_title", "content": "Title"}]},
                    {"page_idx": 1, "blocks": [{"type": "text", "content": "Second page starts now."},
                                               {"type": "image", "content": "", "image_source": "images/x.png"}]}]}
    items = mr.flatten_structured(sc)
    out = mr.insert_page_markers(MD, items)
    assert out.startswith("<!-- page: 1 -->\n# Title") and "<!-- page: 2 -->\nSecond page" in out
