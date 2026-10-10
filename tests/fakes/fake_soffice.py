"""Stands in for soffice.exe in tests: `fake_soffice.py <soffice args...>`.

Behaviour from env FAKE_SOFFICE_MODE: ok (2-page PDF), slow (ok after 0.4 s), fail (exit 1, no output),
noout (exit 0, no output), hang (sleep forever). Every call is appended to FAKE_SOFFICE_LOG as JSON."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def main() -> int:
    argv = sys.argv[1:]
    mode = os.environ.get("FAKE_SOFFICE_MODE", "ok")
    start = time.time()
    outdir = Path(argv[argv.index("--outdir") + 1])
    src = Path(argv[-1])
    if mode == "hang":
        time.sleep(3600)
    if mode == "slow":
        time.sleep(0.4)
    rc = 0
    if mode in ("ok", "slow"):
        import pymupdf as fitz
        doc = fitz.open()
        for i in range(2):
            page = doc.new_page()
            page.insert_text((72, 72), f"{src.name} page {i + 1}")
        outdir.mkdir(parents=True, exist_ok=True)
        doc.save(outdir / f"{src.stem}.pdf")
    elif mode == "fail":
        print("Error: source file could not be loaded", file=sys.stderr)
        rc = 1
    log = os.environ.get("FAKE_SOFFICE_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps({"argv": argv, "src": str(src), "start": start, "end": time.time()}) + "\n")
    return rc


if __name__ == "__main__":
    sys.exit(main())
