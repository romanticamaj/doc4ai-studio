"""Real documents dropped into tests/fixtures/manual/ (gitignored) are converted when present."""
import json
import shutil
from pathlib import Path
import pytest
from aidoc.cli import main

pytestmark = pytest.mark.slow

MANUAL = Path(__file__).parent.parent / "fixtures" / "manual"
FILES = sorted(p for p in MANUAL.iterdir() if p.is_file() and not p.name.startswith(".")) if MANUAL.is_dir() else []


@pytest.mark.skipif(not FILES, reason="no files in tests/fixtures/manual/")
@pytest.mark.parametrize("path", FILES, ids=[p.name for p in FILES])
def test_manual_fixture_ok(tmp_root, path):
    src = tmp_root / path.name
    shutil.copy(path, src)
    assert main(["convert", str(src), "-o", str(tmp_root / "out"), "--json"]) == 0
    sc = json.loads((tmp_root / "out" / src.stem / f"{src.stem}.json").read_text(encoding="utf-8"))
    print(json.dumps(sc, ensure_ascii=False, indent=2))
    assert sc["quality"]["level"] == "ok", sc["quality"]
