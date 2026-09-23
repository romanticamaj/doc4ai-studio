import json
import shutil

import pytest

from aidoc.cli import main

pytestmark = pytest.mark.slow


def test_scanned_english_with_lang_en_default_ocr(tmp_root, fixtures):
    """§13.2 as revised in P2: with the default RapidOCR, lang=en routes like cht (MinerU first) and is ok."""
    src = tmp_root / "scanned_en.pdf"; shutil.copy(fixtures / "scanned_en.pdf", src)
    assert main(["convert", str(src), "-o", str(tmp_root / "out"), "--lang", "en", "--json"]) == 0
    sc = json.loads((tmp_root / "out" / "scanned_en" / "scanned_en.json").read_text(encoding="utf-8"))
    assert sc["engine"] == "mineru" and sc["quality"]["level"] == "ok" and sc["lang"] == "en"
