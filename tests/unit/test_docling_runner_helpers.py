import importlib.util
import sys
from types import SimpleNamespace as NS

from aidoc import paths

sys.path.insert(0, str(paths.runner_dir()))
spec = importlib.util.spec_from_file_location("docling_runner", paths.runner_script("docling"))
dr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dr)


def test_conversion_errors_are_reported_per_page():
    res = NS(errors=[NS(page_no=3, error_message="Page failed to parse."), NS(page_no=None, error_message="timeout"),
                     NS(page_no=3, error_message="again"), NS(page_no=7, error_message="OOM in layout")])
    failed, errors = dr.conversion_errors(res)
    assert failed == {3: "Page failed to parse.", 7: "OOM in layout"}
    assert errors == ["p3: Page failed to parse.", "timeout", "p3: again", "p7: OOM in layout"]
    assert dr.conversion_errors(NS(errors=[])) == ({}, [])
