import hashlib
from aidoc.names import sanitize_stem, file_sha256, sha8


def test_reserved_names():
    assert sanitize_stem("CON") == "CON_"
    assert sanitize_stem("nul") == "nul_"
    assert sanitize_stem("COM1") == "COM1_"


def test_illegal_chars_and_trailing():
    # plan text expected "a_b__c..." but '>:"' are three illegal chars -> three underscores (see deviations)
    assert sanitize_stem('a<b>:"c/d\\e|f?g*h') == "a_b___c_d_e_f_g_h"
    assert sanitize_stem("report..") == "report"
    assert sanitize_stem("  spaced  ") == "spaced"
    assert sanitize_stem("") == "untitled"
    assert sanitize_stem("...") == "untitled"


def test_cjk_preserved():
    assert sanitize_stem("報告 v2") == "報告 v2"


def test_sha256_streaming(tmp_path):
    p = tmp_path / "f.bin"
    data = b"x" * (3 * 1024 * 1024 + 17)
    p.write_bytes(data)
    assert file_sha256(p) == hashlib.sha256(data).hexdigest()
    assert sha8(file_sha256(p)) == hashlib.sha256(data).hexdigest()[:8]
