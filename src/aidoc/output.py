"""Output directory choice, sidecar JSON, atomic finalisation and cache lookup (spec §5, §8.6)."""
from __future__ import annotations
import json
import shutil
from pathlib import Path

from aidoc import __version__, fsops
from aidoc.names import sha8
from aidoc.store import Store

IGNORED_ENTRIES = {".tmp", ".trash", "_manifest.jsonl", "chunks.jsonl"}


def sidecar_path(output_dir: Path) -> Path:
    output_dir = Path(output_dir)
    return output_dir / f"{output_dir.name}.json"


def read_sidecar(output_dir: Path) -> dict | None:
    p = sidecar_path(output_dir)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def choose_output_dir(store: Store, out_root: Path, stem: str, sha256: str) -> Path:
    """`out_root/stem`, or `out_root/stem-<sha8>` when that dir already belongs to different content."""
    candidate = Path(out_root) / stem
    row = store.get_document_by_output(str(candidate))
    if row is not None and row["sha256"] != sha256:
        return Path(out_root) / f"{stem}-{sha8(sha256)}"
    sc = read_sidecar(candidate)
    if sc is not None and sc.get("sha256") not in (None, sha256):
        return Path(out_root) / f"{stem}-{sha8(sha256)}"
    return candidate


def lookup_cached(store: Store, sha256: str, output_dir: Path) -> dict | None:
    """Cache hit = same content already converted into output_dir and its sidecar is still on disk."""
    output_dir = Path(output_dir)
    sc = read_sidecar(output_dir)
    row = store.get_document_by_output(str(output_dir))
    if row is not None:
        if row["sha256"] == sha256 and row["status"] in ("ok", "low") and sc is not None \
                and sc.get("sha256") == sha256:
            return row
        return None
    if sc is not None and sc.get("sha256") == sha256:
        return sc
    return None


def build_sidecar(*, source, sha256, pages, engine, tried, segments, probe, quality, lang, elapsed_s) -> dict:
    return {"source": str(source), "sha256": sha256, "pages": pages, "engine": engine, "tried": tried,
            "segments": segments, "probe": probe, "quality": quality, "lang": lang,
            "elapsed_s": round(float(elapsed_s), 3), "aidoc_version": __version__}


def list_output_dirs(out_root: Path) -> list[Path]:
    out_root = Path(out_root)
    if not out_root.is_dir():
        return []
    return sorted((p for p in out_root.iterdir() if p.is_dir() and p.name not in IGNORED_ENTRIES),
                  key=lambda p: p.name)


class OutputWriter:
    """Builds the output in out/.tmp/<task_id>/ and moves it into place atomically."""

    def __init__(self, out_root: Path, task_id: str, stem: str):
        self.out_root = Path(out_root)
        self.task_id = task_id
        self.stem = stem
        self.tmp_dir = self.out_root / ".tmp" / task_id
        self.trash_dir = self.out_root / ".trash" / task_id

    def write(self, markdown: str, assets: list[tuple[Path, str]], sidecar: dict) -> None:
        if self.tmp_dir.exists():
            fsops.remove_tree(self.tmp_dir)
        (self.tmp_dir / "assets").mkdir(parents=True, exist_ok=True)
        for src, name in assets:
            shutil.copy2(src, self.tmp_dir / "assets" / name)
        fsops.atomic_write_text(self.tmp_dir / f"{self.stem}.md", markdown)
        fsops.atomic_write_json(self.tmp_dir / f"{self.stem}.json", sidecar)

    def finalize(self, final_dir: Path) -> None:
        fsops.replace_dir_three_step(self.tmp_dir, Path(final_dir), self.trash_dir)
        for parent in (self.tmp_dir.parent, self.trash_dir.parent):   # drop empty .tmp/.trash
            try:
                parent.rmdir()
            except OSError:
                pass

    def discard(self) -> None:
        try:
            fsops.remove_tree(self.tmp_dir)
        except fsops.FsBusyError:
            pass
