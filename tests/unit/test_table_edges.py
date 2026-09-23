"""P2 verifier I2: a table 'touches the edge' of its page when nothing but page furniture lies beyond it, measured
against the page's content, not the physical page (1-inch margins are ~8.6 % of the page)."""
import importlib.util
import sys

from aidoc import paths

sys.path.insert(0, str(paths.runner_dir()))
import _proto  # noqa: E402

_spec = importlib.util.spec_from_file_location("mineru_runner", paths.runner_script("mineru"))
mr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mr)


def test_bottom_table_with_one_inch_margin_touches():
    others = [(0.07, 0.09), (0.10, 0.14)]                    # heading + paragraph above the table
    assert _proto.touches_content_edge((0.23, 0.914), others, top=False) is True
    assert _proto.touches_content_edge((0.23, 0.914), others + [(0.93, 0.95)], top=False) is False   # text below


def test_top_table_touches_unless_something_starts_above():
    assert _proto.touches_content_edge((0.083, 0.215), [(0.23, 0.27)], top=True) is True
    assert _proto.touches_content_edge((0.30, 0.50), [(0.10, 0.12)], top=True) is False              # caption above


def test_mineru_middle_json_margins_and_page_number():
    middle = {"pages": [
        {"page_idx": 0, "blocks": [{"type": "paragraph_title", "bbox": [0.117, 0.072, 0.293, 0.089]},
                                   {"type": "text", "bbox": [0.116, 0.1, 0.881, 0.137]},
                                   {"type": "table", "bbox": [0.118, 0.233, 0.879, 0.914],
                                    "content": "| a | b | c |\n|---|---|---|"},
                                   {"type": "page_number", "bbox": [0.491, 0.947, 0.514, 0.959]}]},
        {"page_idx": 1, "blocks": [{"type": "table", "bbox": [0.119, 0.083, 0.878, 0.215],
                                    "content": "| a | b | c |\n|---|---|---|"},
                                   {"type": "text", "bbox": [0.116, 0.23, 0.88, 0.26]},
                                   {"type": "page_number", "bbox": [0.49, 0.947, 0.513, 0.959]}]}]}
    first, last = mr.table_edges(middle)
    assert first == {"page": 1, "n_cols": 3, "touches_edge": False}      # text above it on page 1
    assert last == {"page": 2, "n_cols": 3, "touches_edge": False}       # text below it on page 2
    seg0 = {"pages": middle["pages"][:1]}
    seg1 = {"pages": [dict(middle["pages"][1], page_idx=0)]}
    assert mr.table_edges(seg0)[1]["touches_edge"] is True               # only a page number below
    assert mr.table_edges(seg1)[0]["touches_edge"] is True               # nothing above
