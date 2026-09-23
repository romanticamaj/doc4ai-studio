from __future__ import annotations
import hashlib
import re
from pathlib import Path

RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_stem(name: str) -> str:
    """Make a Windows-safe directory/file stem (spec §5)."""
    s = _ILLEGAL.sub("_", name).strip()
    s = s.rstrip(". ").strip()
    if not s:
        return "untitled"
    if s.split(".")[0].upper() in RESERVED:
        s = s + "_"
    return s


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha8(sha256: str) -> str:
    return sha256[:8]
