"""Docling's pypdfium backend + FULL_PAGE OCR repairs broken text layers (spec 2026-10-01 §3.5, §6.2)."""
import json
import os
import subprocess

import pytest

from aidoc import paths
from tests.manual import manual_sample, require_engine

pytestmark = pytest.mark.slow

SCRIPT = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
import docling_runner as R
if __name__ == '__main__':
    base = {"ocr": "rapidocr", "lang": ["ch_tra", "en"], "page_batch_size": 4}
    out = {}
    for name, extra in (("default", {}), ("fullpage_only", {"full_page_ocr": True}),
                        ("pypdfium", {"full_page_ocr": True, "backend": "pypdfium"})):
        doc = R.build_converter({**base, **extra}).convert(sys.argv[2]).document
        out[name] = doc.export_to_markdown(page_no=1)
    print(json.dumps(out))
"""


def _run(pdf):
    env = {**os.environ, "PYTHONUTF8": "1", "AIDOC_MODELS_DIR": str(paths.models_dir()),
           "HF_HOME": str(paths.models_dir() / "hf")}
    r = subprocess.run([str(paths.venv_python("docling")), "-c", SCRIPT, str(paths.runner_dir()), str(pdf)],
                       capture_output=True, text=True, encoding="utf-8", env=env, check=True)
    return json.loads(r.stdout.strip().splitlines()[-1])


def norm(s):
    return " ".join(s.lower().replace("\N{RIGHT SINGLE QUOTATION MARK}", "'").split())


def test_synthetic_broken_tounicode(fixtures):
    require_engine("docling")
    out = _run(fixtures / "broken_tounicode.pdf")
    assert "dreamy" not in norm(out["default"]) and "wouldn't it be dreamy" in norm(out["pypdfium"])


def test_t001_cover_needs_pypdfium_backend():
    require_engine("docling")
    out = _run(manual_sample("hfad_cover_p1.pdf"))
    assert "wouldn't it be dreamy" not in norm(out["default"])
    assert "wouldn't it be dreamy" not in norm(out["fullpage_only"])        # spec §3.5: FULL_PAGE alone fails
    assert "wouldn't it be dreamy" in norm(out["pypdfium"])
