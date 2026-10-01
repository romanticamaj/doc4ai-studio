"""Write the real samples of manual_samples.json into tests/fixtures/manual/ (never committed: copyrighted books).

    uv run python tests/fixtures/make_manual.py [--db data/aidoc.db] [--source <sha8>=<path> ...]

Each sample's source is found by sha256: a `--source` given on the command line, a document in the database (its
retained work copy `src.*`, else its source path), or a PDF in tests/fixtures/manual/originals/. Every candidate
is verified with sha256 before use. Prints one line per sample; exit 1 when a source is missing.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import pymupdf

HERE = Path(__file__).parent
MANIFEST = HERE / "manual_samples.json"
OUT = HERE / "manual"
ROOT = HERE.parent.parent


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def page_list(spec) -> list:
    """"1-80" -> [1..80]; "1" -> [1]; a list stays a list (ints and "blank")."""
    if isinstance(spec, list):
        return spec
    a, _, b = str(spec).partition("-")
    return list(range(int(a), int(b or a) + 1))


def candidates(sha: str, db: Path, given: dict[str, Path]) -> list[Path]:
    out = [p for k, p in given.items() if sha.startswith(k)]
    if db.is_file():
        con = sqlite3.connect(db)
        try:
            for wc, src in con.execute("SELECT work_copy_path, source_path FROM documents WHERE sha256=?", (sha,)):
                if wc and Path(wc).is_dir():
                    out += sorted(Path(wc).glob("src.*"))
                if src and Path(src).is_absolute():
                    out.append(Path(src))
        finally:
            con.close()
    originals = OUT / "originals"
    if originals.is_dir():
        out += sorted(originals.glob("*.pdf"))
    return out


def resolve(sha: str, db: Path, given: dict[str, Path], cache: dict[str, Path | None]) -> Path | None:
    if sha not in cache:
        cache[sha] = next((p for p in candidates(sha, db, given) if p.is_file() and sha256(p) == sha), None)
    return cache[sha]


def write_sample(src: Path, pages: list, dst: Path) -> None:
    with pymupdf.open(src) as doc, pymupdf.open() as out:
        for p in pages:
            if p == "blank":                           # an empty page the size of the previous one
                rect = out[-1].rect if out.page_count else doc[0].rect
                out.new_page(width=rect.width, height=rect.height)
                continue
            out.insert_pdf(doc, from_page=int(p) - 1, to_page=int(p) - 1)
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(f".{dst.name}.tmp")
        out.save(tmp, garbage=3, deflate=True)
    tmp.replace(dst)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=str(ROOT / "data" / "aidoc.db"))
    ap.add_argument("--source", action="append", default=[], metavar="SHA8=PATH")
    args = ap.parse_args(argv)
    given = {}
    for s in args.source:
        k, _, v = s.partition("=")
        given[k.strip().lower()] = Path(v)
    cache: dict[str, Path | None] = {}
    missing = 0
    for sample in json.loads(MANIFEST.read_text(encoding="utf-8")):
        sha = sample["source_sha256"]
        src = resolve(sha, Path(args.db), given, cache)
        if src is None:
            print(f"missing source {sha[:8]} for {sample['name']} ({sample.get('source_hint', '')})")
            missing += 1
            continue
        pages = page_list(sample["pages"])
        write_sample(src, pages, OUT / sample["name"])
        print(f"ok {sample['name']} {len(pages)} pages")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
