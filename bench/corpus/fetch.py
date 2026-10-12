"""Tier B corpus: real documents under open licences, fetched by URL and verified by sha256 (spec 2026-10-05 verify
§3.2 Tier B). Only MANIFEST.json is committed; the PDFs land in `bench/corpus/b/<name>/` (gitignored).

    uv run python bench/corpus/fetch.py [--check] [--only NAME[,NAME]] [--out bench/corpus/b]

`--check` validates the manifest only (every entry needs a licence from the allowed list and its licence URL).
A missing / changed file never aborts the run: the entry is marked in `fetch.status.json` and the report lists it.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

MANIFEST = HERE / "MANIFEST.json"
LICENSES = ("OGDL-1.0", "CC-BY-4.0", "CC-BY-SA-4.0", "CC0-1.0", "US-PD", "PD", "research-only-no-redistribution")
REQUIRED = ("name", "tier", "url", "license", "license_url", "fetched_on", "sha256", "pages", "kind", "lang",
            "purpose", "notes", "drop_pages")
# ppg.ly.gov.tw answers 403 to non-browser user agents (checked 2026-10-12)
BROWSER_UA = "Mozilla/5.0 (compatible; doc4ai-bench/1; +reproducible corpus)"
_SHA = re.compile(r"^[0-9a-f]{64}$")
_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def check_manifest(entries: list[dict]) -> list[str]:
    problems: list[str] = []
    seen: set[str] = set()
    for i, e in enumerate(entries):
        where = f"entry {i} ({e.get('name', '?')})"
        for k in REQUIRED:
            if k not in e:
                problems.append(f"{where}: missing {k}")
        name = str(e.get("name", ""))
        if not _NAME.match(name):
            problems.append(f"{where}: name must be lower-case kebab")
        if name in seen:
            problems.append(f"{where}: duplicate name")
        seen.add(name)
        if e.get("license") not in LICENSES:
            problems.append(f"{where}: license {e.get('license')!r} not allowed (one of {', '.join(LICENSES)})")
        if not str(e.get("license_url", "")).startswith("https://"):
            problems.append(f"{where}: license_url must be an https URL")
        if not str(e.get("url", "")).startswith("https://"):
            problems.append(f"{where}: url must be https")
        if not _SHA.match(str(e.get("sha256", ""))):
            problems.append(f"{where}: sha256 must be 64 lower-case hex")
        if not _DATE.match(str(e.get("fetched_on", ""))):
            problems.append(f"{where}: fetched_on must be YYYY-MM-DD")
        if not isinstance(e.get("pages"), int) or e.get("pages", 0) < 1:
            problems.append(f"{where}: pages must be a positive int")
        if e.get("kind") not in ("text", "scan"):
            problems.append(f"{where}: kind must be text or scan")
        if e.get("lang") not in ("cht", "en"):
            problems.append(f"{where}: lang must be cht or en")
        if not isinstance(e.get("drop_pages"), list) or not all(isinstance(p, int) for p in e.get("drop_pages", [])):
            problems.append(f"{where}: drop_pages must be a list of ints")
        if e.get("tier") != "B":
            problems.append(f"{where}: tier must be B")
    return problems


def _default_opener(url: str, timeout=None):
    req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA})
    return urllib.request.urlopen(req, timeout=timeout)


def fetch_all(entries: list[dict], out: Path, *, opener=_default_opener, log=print) -> dict[str, str]:
    """Download missing files, verify sha256, drop listed pages (PII removal), write gt.json (L3). Returns
    {name: "ok" | "missing" | "sha_mismatch" | "bad_pdf"} and writes `fetch.status.json` next to `out`."""
    import pymupdf
    status: dict[str, str] = {}
    out.mkdir(parents=True, exist_ok=True)
    for e in entries:
        d = out / e["name"]
        pdf = d / f"{e['name']}.pdf"
        try:
            if pdf.is_file() and (d / "gt.json").is_file() and \
                    json.loads((d / "gt.json").read_text(encoding="utf-8")).get("source_sha256") == e["sha256"]:
                status[e["name"]] = "ok"
                continue
            try:
                with opener(e["url"], timeout=120) as r:
                    data = r.read()
            except OSError as ex:
                log(f"{e['name']}: missing ({type(ex).__name__}: {ex})")
                status[e["name"]] = "missing"
                continue
            got = hashlib.sha256(data).hexdigest()
            if got != e["sha256"]:
                log(f"{e['name']}: sha256 mismatch ({got}); the source changed — re-check the licence and the sha")
                status[e["name"]] = "sha_mismatch"
                continue
            try:
                doc = pymupdf.open(stream=io.BytesIO(data), filetype="pdf")
            except Exception:  # noqa: BLE001  PyMuPDF raises many types
                status[e["name"]] = "bad_pdf"
                continue
            if e["drop_pages"]:
                doc.delete_pages([p - 1 for p in sorted(e["drop_pages"])])
            d.mkdir(parents=True, exist_ok=True)
            doc.save(pdf, garbage=3, deflate=True)
            pages = doc.page_count
            doc.close()
            gt = {"name": e["name"], "tier": "B", "gt_level": "L3", "pages": pages, "kind": e["kind"],
                  "lang": e["lang"], "license": e["license"], "license_url": e["license_url"], "url": e["url"],
                  "source_sha256": e["sha256"], "dropped_for_privacy": e["drop_pages"], "purpose": e["purpose"],
                  "fetched_on": e["fetched_on"]}
            (d / "gt.json").write_text(json.dumps(gt, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            status[e["name"]] = "ok"
            log(f"{e['name']}: ok ({pages} pages)")
        except OSError as ex:
            log(f"{e['name']}: failed: {ex}")
            status[e["name"]] = "missing"
    (out.parent / "fetch.status.json").write_text(json.dumps({"date": datetime.now().astimezone().date().isoformat(), "status": status},
                                                              indent=1) + "\n", encoding="utf-8")
    return status


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="validate MANIFEST.json and exit")
    ap.add_argument("--only", help="comma-separated names")
    ap.add_argument("--out", default=str(HERE / "b"))
    a = ap.parse_args(argv)
    entries = json.loads(MANIFEST.read_text(encoding="utf-8"))
    problems = check_manifest(entries)
    for p in problems:
        print(f"manifest: {p}", file=sys.stderr)
    if problems or a.check:
        return 1 if problems else 0
    if a.only:
        want = set(a.only.split(","))
        entries = [e for e in entries if e["name"] in want]
    entries = [e for e in entries if e["license"] != "research-only-no-redistribution"]
    st = fetch_all(entries, Path(a.out))
    bad = {k: v for k, v in st.items() if v != "ok"}
    print(f"{len(st) - len(bad)}/{len(st)} ok" + (f"; not ok: {bad}" if bad else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
