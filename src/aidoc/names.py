from __future__ import annotations

import hashlib
import re
from pathlib import Path

_DIGITS = [*(str(i) for i in range(10)), "¹", "²", "³"]
RESERVED = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",
            *(f"COM{d}" for d in _DIGITS), *(f"LPT{d}" for d in _DIGITS)}
# names the output root uses for its own bookkeeping (output.IGNORED_ENTRIES)
_BOOKKEEPING = {".tmp", ".trash", "_manifest.jsonl", "chunks.jsonl"}
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# NTFS allows 255 chars per component; keep room for "-<sha8>" collision suffixes and ".<name>.json.tmp" temp files
MAX_STEM = 150


def sanitize_stem(name: str) -> str:
    """Make a Windows-safe directory/file stem (spec §5); at most MAX_STEM chars."""
    s = _ILLEGAL.sub("_", name).strip()
    s = s[:MAX_STEM].rstrip(". ").strip()
    if not s:
        return "untitled"
    if s.startswith("."):                     # never hidden, never out/.tmp or out/.trash
        s = "_" + s[1:]
    if s.lower() in _BOOKKEEPING:
        s = s + "_"
    if s.split(".")[0].rstrip().upper() in RESERVED:
        s = s + "_"
    return s


def output_stem(name: str, sha256: str) -> str:
    """Stem for an output dir: sanitize_stem, and when the name had to be shortened, `<prefix>-<sha8>`
    so two long names sharing a prefix do not collide."""
    full = _ILLEGAL.sub("_", name).strip().rstrip(". ").strip()
    s = sanitize_stem(name)
    if len(full) <= MAX_STEM:
        return s
    return sanitize_stem(s[: MAX_STEM - 9]) + "-" + sha8(sha256)


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha8(sha256: str) -> str:
    return sha256[:8]
