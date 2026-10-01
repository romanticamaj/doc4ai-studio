"""Real documents in tests/fixtures/manual/ (gitignored) convert to ok or warn.

The samples listed in manual_samples.json are required in acceptance mode (AIDOC_REQUIRE_MANUAL=1: a missing one
fails, spec 2026-10-01 §10.2); any other file dropped into the folder is converted too when present."""
import json
import shutil
from pathlib import Path

import pytest

from aidoc.cli import main
from tests.manual import MANIFEST, manual_sample, require_engine

pytestmark = pytest.mark.slow

MANUAL = Path(__file__).parent.parent / "fixtures" / "manual"
LISTED = [s["name"] for s in json.loads(MANIFEST.read_text(encoding="utf-8"))]
EXTRA = sorted(p for p in MANUAL.iterdir() if p.is_file() and not p.name.startswith(".")
               and p.name not in LISTED) if MANUAL.is_dir() else []


def _convert_ok(tmp_root, path: Path) -> None:
    for engine in ("markitdown", "docling", "mineru"):
        require_engine(engine)
    src = tmp_root / path.name
    shutil.copy(path, src)
    assert main(["convert", str(src), "-o", str(tmp_root / "out"), "--json"]) == 0
    sc = json.loads((tmp_root / "out" / src.stem / f"{src.stem}.json").read_text(encoding="utf-8"))
    print(json.dumps(sc, ensure_ascii=False, indent=2))
    assert sc["quality"]["level"] in ("ok", "warn"), sc["quality"]


@pytest.mark.parametrize("name", LISTED)
def test_listed_manual_sample_converts(tmp_root, name):
    _convert_ok(tmp_root, manual_sample(name))


if EXTRA:                       # defined only when there is something extra (no empty-parameter skip in acceptance)
    @pytest.mark.parametrize("path", EXTRA, ids=[p.name for p in EXTRA])
    def test_extra_manual_file_converts(tmp_root, path):
        _convert_ok(tmp_root, path)
