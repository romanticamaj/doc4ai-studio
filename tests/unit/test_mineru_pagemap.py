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


A, B, C, D = ("甲" * 120, "乙" * 160, "丙" * 430, "丁" * 300)


def test_a_chain_that_drains_whole_pages_is_rendered_page_by_page():
    """T-002 root cause (MinerU on the 1973 court letter): blocks flagged continues_prev on pages 2-3 are merged by
    the DEFAULT render plan into page 1, leaving pages 2 and 3 empty. The chain is rendered page by page."""
    plan = [A + "\n\n" + B + C, "", "", D]
    ref = "\n\n".join(p for p in plan if p)
    calls = []

    def single(mj):
        calls.append(1)
        return [A, B, C, D]
    pages, method = mr.render_pages(object(), reference=ref, plan=lambda mj: plan, single=single)
    assert pages == [A, B, C, D] and method == "mineru_render_plan" and calls == [1]


def test_a_drained_last_page_takes_its_table_back():
    """MinerU on the AASHTO report: page 5's table continues page 4's and is merged into page 4."""
    plan = [A, B, C, D + "\n\n" + B, ""]
    ref = "\n\n".join(p for p in plan if p)
    pages, _ = mr.render_pages(object(), reference=ref, plan=lambda mj: plan, single=lambda mj: [A, B, C, D, B])
    assert pages == [A, B, C, D, B]


def test_a_paragraph_tail_merged_across_a_page_break_is_kept():
    """The documented DEFAULT behaviour (spec 2026-10-01 §4): a continued paragraph's tail on the page it started;
    no page is drained, so the plan stands and no single-page render is made."""
    plan = [A + "tail", B, C]
    ref = "\n\n".join(plan)
    pages, _ = mr.render_pages(object(), reference=ref, plan=lambda mj: plan,
                               single=lambda mj: (_ for _ in ()).throw(AssertionError("not needed")))
    assert pages == plan


def test_a_blank_page_stays_blank():
    plan = [A, "", C]
    pages, _ = mr.render_pages(object(), reference=A + "\n\n" + C, plan=lambda mj: plan,
                               single=lambda mj: [A, "", C])
    assert pages == plan
