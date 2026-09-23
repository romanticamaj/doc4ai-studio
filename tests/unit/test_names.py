import hashlib

from aidoc.names import file_sha256, sanitize_stem, sha8


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


def test_more_reserved_names():
    """P1 deferred: CONIN$, CONOUT$ and superscript COM/LPT digits are reserved on Windows too."""
    from aidoc.names import sanitize_stem as s
    for name in ("CONIN$", "conout$", "COM¹", "LPT³", "COM0", "LPT0", "CON .tar", "aux.backup"):
        assert s(name).endswith("_"), name
    assert s("CONSOLE") == "CONSOLE" and s("COM10") == "COM10"


def test_stems_never_collide_with_output_bookkeeping():
    """P1 deferred: a source named .tmp.pdf / .trash.pdf must not land in out/.tmp or out/.trash."""
    from aidoc.names import sanitize_stem as s
    assert s(".tmp") == "_tmp" and s(".trash") == "_trash" and s(".hidden") == "_hidden"
    assert s("chunks.jsonl") != "chunks.jsonl" and s("_manifest.jsonl") != "_manifest.jsonl"


def test_long_stems_truncated_with_sha8():
    """P1 verifier 2: NTFS components are limited to 255 chars; temp names add suffixes."""
    from aidoc.names import MAX_STEM, output_stem
    from aidoc.names import sanitize_stem as s
    assert len(s("b" * 248)) <= MAX_STEM <= 180
    sha = "0123456789abcdef" * 4
    st = output_stem("b" * 248, sha)
    assert len(st) <= MAX_STEM and st.endswith("-01234567")
    assert output_stem("short", sha) == "short"
    assert output_stem("中" * 200, sha).endswith("-01234567")
