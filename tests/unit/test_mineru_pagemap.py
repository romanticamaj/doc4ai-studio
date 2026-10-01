import importlib.util
import sys

from aidoc import paths

sys.path.insert(0, str(paths.runner_dir()))
spec = importlib.util.spec_from_file_location("mineru_runner", paths.runner_script("mineru"))
mr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mr)

PAGES = ["# Title\n\nFirst page.", "", "Third page with ![](images/a.jpg)"]
REF = "# Title\n\nFirst page.\n\nThird page with ![](images/a.jpg)"


def test_assemble_marks_every_page_including_blank():
    out = mr.assemble(PAGES)
    assert out == ("<!-- page: 1 -->\n\n# Title\n\nFirst page.\n\n<!-- page: 2 -->\n\n"
                   "<!-- page: 3 -->\n\nThird page with ![](images/a.jpg)\n\n")


def test_render_plan_used_when_identical_to_reference():
    pages, method = mr.render_pages(object(), reference=REF, plan=lambda mj: PAGES, single=lambda mj: ["x"] * 3)
    assert method == "mineru_render_plan" and pages == PAGES


def test_falls_back_to_single_page_render_on_mismatch_or_error():
    single = lambda mj: ["s1", "s2", "s3"]
    assert mr.render_pages(object(), reference=REF + "!", plan=lambda mj: PAGES, single=single) == (
        ["s1", "s2", "s3"], "mineru_single_page")

    def boom(mj):
        raise ImportError("docvortex internals moved")
    assert mr.render_pages(object(), reference=REF, plan=boom, single=single)[1] == "mineru_single_page"


def test_no_text_search_left():
    src = paths.runner_script("mineru").read_text(encoding="utf-8")
    assert "insert_page_markers" not in src and "_needles" not in src
