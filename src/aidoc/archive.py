"""Decompression-bomb check for ZIP-based inputs (verifier round, item 1a). Runs on the staged work copy before
any engine: every entry point (web upload, CLI, MCP, compat API) reaches the pipeline through run_task.

Python's zipfile (and the libraries built on it: python-docx, openpyxl, python-pptx, MarkItDown) stops each entry
at its declared size and fails the CRC otherwise, so the declared sizes bound what an engine can inflate."""
from __future__ import annotations

import zipfile
from pathlib import Path

ZIP_EXTS = frozenset({".docx", ".xlsx", ".pptx", ".docm", ".xlsm", ".pptm", ".epub", ".odt", ".ods", ".odp", ".zip"})
MIB = 1024 * 1024


def check_archive(path: Path, limits, name: str | None = None) -> str | None:
    """None when the file is not a ZIP-based type, is not a readable ZIP (the engines report that), or stays within
    the [limits] archive_* values; else "archive_too_large: <why>". `name`: the file name whose extension counts
    (an upload's part file has none)."""
    path = Path(path)
    if Path(name or path.name).suffix.lower() not in ZIP_EXTS:
        return None
    try:
        with zipfile.ZipFile(path) as z:
            infos = z.infolist()
    except (zipfile.BadZipFile, OSError, ValueError, NotImplementedError):
        return None
    if len(infos) > limits.archive_max_entries:
        return f"archive_too_large: {len(infos)} entries (limit {limits.archive_max_entries})"
    total = sum(i.file_size for i in infos)
    biggest = max((i.file_size for i in infos), default=0)
    if biggest > limits.archive_max_entry_mb * MIB:
        return f"archive_too_large: one entry inflates to {biggest // MIB} MB (limit {limits.archive_max_entry_mb} MB)"
    if total > limits.archive_max_uncompressed_mb * MIB:
        return f"archive_too_large: inflates to {total // MIB} MB in total (limit {limits.archive_max_uncompressed_mb} MB)"
    size = max(1, path.stat().st_size)
    if total / size > limits.archive_max_ratio:
        return (f"archive_too_large: compression ratio {total / size:.0f} (limit {limits.archive_max_ratio}); "
                f"{size} bytes inflate to {total // MIB} MB")
    return None
