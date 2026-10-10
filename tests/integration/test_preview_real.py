"""Office preview with the real LibreOffice (skipped when soffice is not installed)."""
from __future__ import annotations

import hashlib
import time
from pathlib import Path

import pytest

from aidoc import preview as P

pytestmark = pytest.mark.slow

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.fixture(scope="module")
def soffice():
    exe = P.find_soffice("")
    if exe is None:
        pytest.skip("LibreOffice (soffice) is not installed")
    return exe


@pytest.mark.parametrize("name", ["sample.docx", "sample.pptx", "sample.xlsx"])
def test_real_soffice_renders_office_fixtures(tmp_path, soffice, name):
    src = tmp_path / name
    src.write_bytes((FIXTURES / name).read_bytes())
    doc = {"id": "d", "sha256": hashlib.sha256(src.read_bytes()).hexdigest(), "source_path": str(src),
           "work_copy_path": None}
    m = P.PreviewManager(tmp_path / "data", P.PreviewSettings(timeout_s=240))
    try:
        assert m.request(doc)["status"] == "pending"
        deadline = time.time() + 300
        while (st := m.status(doc))["status"] == "pending" and time.time() < deadline:
            time.sleep(0.2)
        assert st["status"] == "ready", st
        assert st["pages"] >= 1
        pdf = m.pdf_path(doc)
        assert pdf.read_bytes()[:5] == b"%PDF-"
        import pymupdf
        with pymupdf.open(pdf) as d:
            text = "".join(p.get_text() for p in d)
        assert len(text.strip()) > 10, text[:200]
        # no temp copies left behind, the source untouched, the profile carries the macro policy
        assert not any((tmp_path / "data" / "previews" / ".tmp").iterdir())
        assert src.read_bytes() == (FIXTURES / name).read_bytes()
        xcu = tmp_path / "data" / "previews" / ".profile" / "user" / "registrymodifications.xcu"
        assert "DisableMacrosExecution" in xcu.read_text(encoding="utf-8")
    finally:
        m.stop()
