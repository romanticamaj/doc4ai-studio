import hashlib
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from bench.corpus import fetch as F

BENCH = Path(__file__).resolve().parents[2] / "bench"
FIX = Path(__file__).resolve().parents[1] / "fixtures"

ENTRY = {"name": "x", "tier": "B", "url": "https://example.org/a.pdf", "license": "CC-BY-4.0",
         "license_url": "https://creativecommons.org/licenses/by/4.0/", "fetched_on": "2026-10-05", "sha256": "0" * 64,
         "pages": 3, "kind": "text", "lang": "cht", "purpose": "t", "notes": "", "drop_pages": []}


def test_manifest_validates_and_has_the_planned_sources():
    entries = json.loads((BENCH / "corpus" / "MANIFEST.json").read_text(encoding="utf-8"))
    assert F.check_manifest(entries) == []
    lic = [e["license"] for e in entries]
    assert lic.count("OGDL-1.0") >= 2 and lic.count("CC-BY-4.0") >= 3 and lic.count("US-PD") >= 2
    r = subprocess.run([sys.executable, str(BENCH / "corpus" / "fetch.py"), "--check"], capture_output=True, check=False)
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("bad,msg", [
    ({"license": "CC-BY-NC-4.0"}, "license"),
    ({"license_url": ""}, "license_url"),
    ({"sha256": "abc"}, "sha256"),
    ({"url": "ftp://x"}, "url"),
    ({"fetched_on": "yesterday"}, "fetched_on"),
    ({"tier": "A"}, "tier"),
])
def test_manifest_rejects_bad_entries(bad, msg):
    assert any(msg in p for p in F.check_manifest([{**ENTRY, **bad}]))


def test_manifest_rejects_missing_keys_and_duplicates():
    e = dict(ENTRY)
    del e["license"]
    assert any("missing license" in p for p in F.check_manifest([e]))
    assert any("duplicate" in p for p in F.check_manifest([ENTRY, ENTRY]))


def test_fetch_marks_missing_and_verifies_sha(tmp_path):
    def opener(url, timeout=None):
        raise OSError("404")
    status = F.fetch_all([ENTRY], tmp_path / "b", opener=opener, log=lambda s: None)
    assert status["x"] == "missing"
    assert json.loads((tmp_path / "fetch.status.json").read_text(encoding="utf-8"))["status"] == {"x": "missing"}
    good = (FIX / "text.pdf").read_bytes()
    status = F.fetch_all([ENTRY], tmp_path / "b", opener=lambda u, timeout=None: io.BytesIO(good), log=lambda s: None)
    assert status["x"] == "sha_mismatch"
    e = {**ENTRY, "sha256": hashlib.sha256(good).hexdigest(), "drop_pages": [2]}
    status = F.fetch_all([e], tmp_path / "b", opener=lambda u, timeout=None: io.BytesIO(good), log=lambda s: None)
    gt = json.loads((tmp_path / "b" / "x" / "gt.json").read_text(encoding="utf-8"))
    assert status["x"] == "ok" and gt["gt_level"] == "L3" and gt["pages"] == 2 and gt["dropped_for_privacy"] == [2]
    assert gt["license"] == "CC-BY-4.0" and gt["license_url"].startswith("https://")
