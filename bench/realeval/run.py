"""Real-sample evaluation runner (spec 2026-10-22). Not part of the `aidoc` package; changes nothing under src/.

    uv run python bench/realeval/run.py --part scans|formulas|vlm|all --out bench/results/<date>-realeval
        [--root tests/fixtures/manual/realeval] [--resume] [--decide-only] [--offline-declared]
        [--vlm-artefacts <dir> [--vlm-artefacts <dir> ...]]      # real VLM part: the harness launches llama-server
        [--fake --stand-in <dir> --vlm-base-url http://127.0.0.1:<port>/v1]   # dry run: stand-ins + fake engines
        [--rerun-pair <config>/<doc> --reason "..."]               # spec §8.3: one harness-caused rerun, recorded

Every conversion is `bench.run.convert` (one `aidoc convert --force --json` per (config, document), own data dir,
own output root, `aidoc-<config>.toml` with `[vlm] backend = "none"`, single engines with AIDOC_BENCH_NO_REPAIR=1)
with `--lang` from the manifest. After every part, validity.json, decisions.json and summary.md are recomputed from
the files on disk — the same function `--decide-only` runs for the verifier (spec §13 #6)."""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

from bench import run as B
from bench.metrics import plain
from bench.realeval import formulas as F
from bench.realeval import report, rules, samples, scoring
from bench.realeval import vlm_measure as V
from bench.realeval.gt_from_wikitext import check_text
from bench.realeval.gt_quality import digest_of
from bench.realeval.normalize import apply_errata

CONFIGS = ["auto", "docling", "mineru"]
PARTS = ("scans", "formulas", "vlm")
CONTROL_DOC = "en-aashto-usrn-1984"                     # spec §7.0 check 3
SPEC = "docs/superpowers/specs/2026-10-22-real-sample-eval-design.md"
HARNESS = "bench/realeval/run.py"
COVER_DEFAULT = ROOT / "tests" / "fixtures" / "manual" / "hfad_cover_p1.pdf"
ARTEFACT_FILES = {"llamacpp_zip": "llama-b11559-bin-win-cuda-13.4-x64.zip",
                  "cudart_zip": "cudart-llama-bin-win-cuda-13.4-x64.zip",
                  "gguf": "PaddleOCR-VL-1.6-GGUF.gguf", "mmproj": "PaddleOCR-VL-1.6-GGUF-mmproj.gguf",
                  "server": "llama-server.exe" if os.name == "nt" else "llama-server"}


# ---------------------------------------------------------------- helpers

def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _sha_text(p: Path) -> str | None:
    return samples.sha256_file(p) if p.is_file() else None


def _jsonl(p: Path) -> list[dict]:
    if not p.is_file():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def _append(p: Path, row: dict) -> None:
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_json(p: Path, obj) -> None:
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def _git(*args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True, text=True, cwd=ROOT, check=False)


def _opts(lang: str):
    from aidoc.config import load_config
    from aidoc.models import ConvertOptions
    cfg = load_config()
    return ConvertOptions(output_dir=Path("."), lang=lang, mineru_tier=cfg.engines.mineru_tier,
                          docling_ocr=cfg.engines.docling_ocr)


def route_reason(pdf: Path, lang: str) -> str:
    """The reason the current router gives this PDF (`aidoc.router._rule` on the same probe; the CLI payload does
    not carry it). Recorded beside `tried`, which shows what auto actually ran."""
    from aidoc.probe import probe_file
    from aidoc.router import _rule
    return _rule(probe_file(pdf), _opts(lang))[1]


def base_environment(a) -> dict:
    ns = argparse.Namespace(vlm_base_url=None, vlm_model=None, fake=bool(a.fake), repeat=1)
    return B.environment(ns, CONFIGS, ["realeval"])


def _stage(src: Path, dst: Path) -> Path:
    """`<out>/inputs/<doc>.pdf`: aidoc names output folders after the file stem, and every A/C original is
    `original.pdf`. Hardlink (same bytes, same sha256), else copy."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        if samples.sha256_file(dst) == samples.sha256_file(src):
            return dst
        dst.unlink()
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)
    return dst


def find_artefacts(dirs: list[Path]) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for d in dirs:
        for key, name in ARTEFACT_FILES.items():
            if key in found:
                continue
            hit = next(iter(sorted(Path(d).rglob(name))), None)
            if hit is not None:
                found[key] = hit
    return found


def exe_from_zip(exe: Path | None, zip_path: Path | None) -> bool:
    """The llama-server binary that runs is byte-identical to the one in the pinned zip."""
    if not exe or not zip_path or not exe.is_file() or not zip_path.is_file():
        return False
    with zipfile.ZipFile(zip_path) as z:
        member = next((n for n in z.namelist() if Path(n).name == exe.name), None)
        if member is None:
            return False
        return hashlib.sha256(z.read(member)).hexdigest() == samples.sha256_file(exe)


def _commit_has_spec_and_harness(head: str) -> bool:
    return all(_git("cat-file", "-e", f"{head}:{p}").returncode == 0 for p in (SPEC, HARNESS))


def _manifest(root: Path, fake: bool) -> list[dict]:
    return json.loads(((root / "MANIFEST.json") if fake else samples.MANIFEST).read_text(encoding="utf-8"))


def gt_problems(docs, gt: dict[str, dict[int, str]]) -> list[dict]:
    """Scored GT pages that are near-empty (manifest `short_ok` pages excepted) or still hold markup."""
    out = []
    for s in docs:
        short = set(s.entry.get("short_ok") or [])
        for p, text in sorted((gt.get(s.name) or {}).items()):
            probs = [x for x in check_text(text) if not (x == "near_empty" and p in short)]
            if probs:
                out.append({"doc": s.name, "page": p, "problems": probs})
    return out


class Register:
    """RUNS.json beside the samples (gitignored, outside --out): one registered real run (verifier Task 9, "no
    fishing"). A second run starts only with --invalidated-run <id> naming a registered run whose validity.json
    shows a failed check; --rerun-pair is allowed once per pair and recorded here."""

    def __init__(self, path: Path, out: Path, invalidated: str | None):
        self.path, self.out, self.id = path, Path(out), Path(out).name
        self.data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"runs": []}
        self.mine = next((r for r in self.data["runs"] if r["id"] == self.id), None)
        if self.mine is not None and self.mine["out"] != str(self.out):
            raise SystemExit(f"RUNS.json: run id {self.id} is registered for another folder")
        self.invalidates = None
        if self.mine is None and self.data["runs"]:
            ids = {r["id"]: r for r in self.data["runs"]}
            taken = {r.get("invalidates") for r in self.data["runs"]}
            if not invalidated or invalidated not in ids or invalidated in taken:
                raise SystemExit(f"RUNS.json already holds a registered run ({', '.join(ids)}): the decision samples "
                                 "are used once (spec §8.2); a new run needs --invalidated-run <id> of a run whose "
                                 "validity checks failed")
            v = Path(ids[invalidated]["out"]) / "validity.json"
            checks = json.loads(v.read_text(encoding="utf-8")).get("checks", {}) if v.is_file() else {}
            if not checks or all(checks.values()):
                raise SystemExit(f"run {invalidated}: its validity.json shows no failed validity check")
            self.invalidates = invalidated

    def _save(self) -> None:
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=1) + chr(10), encoding="utf-8")

    def start(self, commit: str | None) -> None:
        if self.mine is None:
            self.mine = {"id": self.id, "out": str(self.out), "started": _now(), "aidoc_commit": commit,
                         "invalidates": self.invalidates, "reruns": []}
            self.data["runs"].append(self.mine)
            self._save()

    def rerun(self, pair: str, reason: str) -> None:
        if self.mine is None:
            raise SystemExit("--rerun-pair needs a registered run (resume it)")
        if any(r["pair"] == pair for r in self.mine["reruns"]):
            raise SystemExit(f"{pair} was already rerun once (RUNS.json; spec §8.3)")
        self.mine["reruns"].append({"date": _now(), "pair": pair, "reason": reason})
        self._save()


def _loopback(host: str | None) -> bool:
    try:
        return ipaddress.ip_address(host or "").is_loopback
    except ValueError:
        return host == "localhost"


# ---------------------------------------------------------------- validity (spec §7.0) and the decision

def validity(env: dict, rows: list[dict], vlm: dict | None) -> dict:
    checks: dict[str, bool] = {}
    details: dict = {}
    derived = env.get("derived") or {}
    checks["samples"] = (not env.get("missing") and len(env.get("samples") or {}) == env.get("expected_samples")
                         and all(d.get("text_chars") == 0 for d in derived.values()))
    details["samples"] = {"present": len(env.get("samples") or {}), "expected": env.get("expected_samples"),
                          "missing": env.get("missing"),
                          "derived_text_chars": {k: d.get("text_chars") for k, d in derived.items()}}
    checks["clean_tree"] = env.get("aidoc_dirty") is False and bool(env.get("commit_has_spec_and_harness"))
    checks["gt_pages"] = "gt_problems" in env and not env["gt_problems"]
    details["gt_pages"] = {"problems": env.get("gt_problems")}
    ctrl = {r["config"]: r.get("ned") for r in rows if r.get("doc") == CONTROL_DOC and r["config"] in
            ("docling", "mineru")}
    best = max((v for v in ctrl.values() if v is not None), default=None)
    checks["control_ned"] = best is not None and best >= rules.CONTROL_VALIDITY_NED - rules.EPS
    details["control_ned"] = {"doc": CONTROL_DOC, "ned": ctrl, "threshold": rules.CONTROL_VALIDITY_NED}
    # check 4 is about the harness's config: a conversion that failed without a payload (timeout, crash) has no
    # `tried` and scores 0 (spec §3); it does not void the run. The router's reason must be pdf_scanned for all.
    auto_rows = [r for r in rows if r.get("group") == "A" and r["config"] == "auto"]
    auto_a = {r["doc"]: (r.get("tried") or [None])[0] for r in auto_rows}
    reasons = {r["doc"]: r.get("route_reason") for r in auto_rows}
    checks["auto_route"] = (bool(auto_rows) and all(t == "mineru" for t in auto_a.values() if t is not None)
                            and any(t is not None for t in auto_a.values())
                            and all(v == "pdf_scanned" for v in reasons.values()))
    details["auto_route"] = {"tried0": auto_a, "route_reason": reasons, "expected": ["mineru", "pdf_scanned"]}
    srv = (vlm or {}).get("server") or {}
    checks["vlm_setup"] = bool(vlm) and bool(srv.get("device_cuda")) and \
        srv.get("layers_on_gpu") == srv.get("layers_total") == rules.VLM_LAYERS and \
        _loopback((vlm or {}).get("endpoint_host")) and bool((vlm or {}).get("pinned_ok"))
    details["vlm_setup"] = {"server": srv, "endpoint_host": (vlm or {}).get("endpoint_host"),
                            "pinned_ok": (vlm or {}).get("pinned_ok")}
    return {"checks": checks, "details": details}


def _route_input(pages: list[dict]) -> dict | None:
    per: dict[str, dict[str, list[dict]]] = {}
    for r in pages:
        if r.get("group") == "A" and r["config"] in ("docling", "mineru"):
            per.setdefault(r["doc"], {}).setdefault(r["config"], []).append(r)
    return scoring.route_stats(per) if per else None


def _formula_input(rows: list[dict]) -> list[dict] | None:
    by: dict[str, dict[str, dict]] = {}
    for r in rows:
        if r.get("group") == "B":
            by.setdefault(r["doc"], {})[r["config"]] = r
    out = []
    for doc, c in by.items():
        m, d, a = c.get("mineru") or {}, c.get("docling") or {}, c.get("auto") or {}
        out.append({"doc": doc, "R_M": m.get("R", 0.0), "R_D": d.get("R", 0.0), "T_M": m.get("T", 0.0),
                    "T_D": d.get("T", 0.0), "n_display": (m or d or a).get("n_display"),
                    "math_hint": (a or m or d).get("math_hint"), "auto_reason": a.get("route_reason"),
                    "auto_engine": a.get("engine_used"),
                    "both_ok": bool(m) and bool(d) and m.get("status") != "failed" and d.get("status") != "failed"})
    return out or None


def decide_from_disk(out: Path) -> tuple[dict, dict]:
    env = json.loads((out / "env.json").read_text(encoding="utf-8"))
    rows = _jsonl(out / "results.jsonl")
    pages = _jsonl(out / "pages.jsonl")
    vr = out / "vlm_result.json"
    vlm = json.loads(vr.read_text(encoding="utf-8")) if vr.is_file() else None
    gq_p = out / "gt_quality.json"
    gq = json.loads(gq_p.read_text(encoding="utf-8")) if gq_p.is_file() else {}
    val = validity(env, rows, vlm)
    return rules.decide(_route_input(pages), _formula_input(rows), vlm, gq, val), val


def _diff(a, b, path: str = "") -> list[str]:
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            out += _diff(a.get(k), b.get(k), f"{path}.{k}" if path else k)
        return out
    return [] if a == b else [f"{path}: {json.dumps(b)} -> {json.dumps(a)}"]


def decide_only(out: Path) -> int:
    dec, _ = decide_from_disk(out)
    old = json.loads((out / "decisions.json").read_text(encoding="utf-8"))
    d = _diff(json.loads(json.dumps(dec)), old)
    if d:
        print("decisions.json differs from a recomputation (recorded -> recomputed):")
        for line in d:
            print("  " + line)
        return 1
    print("no diff")
    return 0


# ---------------------------------------------------------------- the parts

def _done(results: Path) -> set[tuple[str, str]]:
    return {(r["config"], r["doc"]) for r in _jsonl(results)}


def _convert(doc: str, pdf: Path, config: str, out: Path, lang: str, fake: bool, log) -> dict:
    s = B.Sample(doc, "R", pdf, None, "realeval")
    log(f"{config} {doc} ({s.pages} pages)")
    conv = B.convert(s, config, out, fake=fake, log=log, extra_args=("--lang", lang))
    log(f"  -> {conv['status']} {conv.get('engine_used') or '-'} {conv['seconds']:.1f}s")
    return conv


def _base_row(config: str, s, conv: dict) -> dict:
    return {"config": config, "doc": s.name, "group": s.group, "lang": s.lang, "status": conv["status"],
            "engine_used": conv.get("engine_used"), "tried": conv.get("tried"), "seconds": conv["seconds"],
            "peak_vram_mb": conv.get("peak_vram_mb"), "error": (conv.get("error") or None) and conv["error"][-500:]}


def part_scans(docs, gt, staged, out, fake, done, log) -> None:
    for config in CONFIGS:
        for s in docs:
            if s.group not in ("A", "C") or (config, s.name) in done:
                continue
            conv = _convert(s.name, staged[s.name], config, out, s.lang, fake, log)
            md = conv.get("md") if conv["status"] != "failed" else None
            rows = scoring.page_rows(s, gt[s.name], md)
            for r in rows:
                _append(out / "pages.jsonl", {"config": config, "doc": s.name, "group": s.group, **r})
            row = {**_base_row(config, s, conv), **scoring.doc_summary(rows)}
            if config == "auto":
                row["route_reason"] = route_reason(staged[s.name], s.lang)
            _append(out / "results.jsonl", row)


def part_formulas(docs, staged, out, fake, done, log) -> None:
    from aidoc.pagemap import squash
    from aidoc.probe import probe_file
    for s in docs:
        if s.group != "B":
            continue
        todo = [c for c in CONFIGS if (c, s.name) not in done]
        if not todo:
            continue
        g_disp, g_inl = F.display_gt(s.formulas_json), F.inline_gt(s.formulas_json)
        gt_text = F.html_text(s.html)
        hint = probe_file(s.pdf).math_hint
        reason = route_reason(staged[s.name], s.lang)
        for config in todo:
            conv = _convert(s.name, staged[s.name], config, out, s.lang, fake, log)
            md = conv.get("md") if conv["status"] != "failed" else None
            preds = F.pred_formulas(md) if md else []
            rec, rin = F.recovery(g_disp, preds), F.recovery(g_inl, preds)
            t = F.ngram_recall(gt_text, squash(plain(md))) if md else 0.0
            _append(out / "results.jsonl", {**_base_row(config, s, conv), "R": rec["R"], "S": rec["S"],
                                            "matched": rec["matched"], "n_display": rec["n_gt"],
                                            "n_pred": rec["n_pred"], "R_inline": rin["R"], "n_inline": rin["n_gt"],
                                            "T": t, "math_hint": hint, "route_reason": reason})


def part_vlm(docs, gt, a, out, cover: Path | None, log) -> None:
    from bench.gpu_sampler import query_mb
    vdocs = [s for s in docs if s.group in ("A", "C")]
    extra = {"expected_docs": [s.name for s in vdocs], "nvidia_smi": query_mb() is not None}
    if a.fake:
        res = V.measure(vdocs, cover, a.vlm_base_url, out=out, gt_texts=gt)
        extra.update(pinned={}, pinned_ok=False, exe_from_zip=False)
    else:
        art = find_artefacts([Path(d) for d in a.vlm_artefacts])
        pinned = V.check_pinned({k: art.get(k) for k in rules.VLM_PINNED})
        exe_ok = exe_from_zip(art.get("server"), art.get("llamacpp_zip"))
        extra.update(pinned=pinned, pinned_ok=all(pinned.values()) and exe_ok, exe_from_zip=exe_ok,
                     artefacts={k: str(p) for k, p in art.items()})
        if not extra["pinned_ok"]:
            log(f"pinned artefacts do not match (spec §7.3.2 insufficient): {pinned}, exe_from_zip={exe_ok}")
            res = {"per_doc": {}, "max_sec_overall": None, "cover_hit": False, "cover_measured": False,
                   "vram": {"baseline": None, "peak": None, "delta": None}, "load_seconds": None, "server": None,
                   "endpoint_host": None, "launched": False}
        else:
            def factory():
                return V.LlamaServer(art["server"], art["gguf"], art["mmproj"], out / "llama-server.log")
            res = V.measure(vdocs, cover, None, out=out, gt_texts=gt, server_factory=factory)
    srv = res.get("server") or {}
    extra.update(server_cuda=bool(srv.get("device_cuda")),
                 layers=f"{srv.get('layers_on_gpu')}/{srv.get('layers_total')}" if srv else None)
    _write_json(out / "vlm_result.json", {**res, **extra})
    log(f"vlm: {len(res.get('per_doc') or {})} documents, max {res.get('max_sec_overall')} s, "
        f"vram {res.get('vram')}")


# ---------------------------------------------------------------- main

def _parse(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--part", choices=[*PARTS, "all"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--root", help="realeval sample folder (default tests/fixtures/manual/realeval)")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--decide-only", action="store_true")
    ap.add_argument("--fake", action="store_true")
    ap.add_argument("--stand-in", help="--fake only: a synthetic folder shaped like the real one, with MANIFEST.json")
    ap.add_argument("--vlm-base-url", help="--fake only: the fake endpoint")
    ap.add_argument("--vlm-artefacts", action="append", default=[], help="folder(s) holding the pinned files")
    ap.add_argument("--cover-pdf", help=f"default {COVER_DEFAULT.relative_to(ROOT)}")
    ap.add_argument("--offline-declared", action="store_true")
    ap.add_argument("--rerun-pair", help="<config>/<doc>: rerun one pair after a harness failure (spec §8.3)")
    ap.add_argument("--reason")
    ap.add_argument("--invalidated-run", help="id of a registered run whose validity.json shows a failed check")
    a = ap.parse_args(argv)
    if a.decide_only:
        return a
    if not a.part:
        ap.error("--part is required")
    if a.fake and not a.stand_in:
        raise SystemExit("--fake needs --stand-in <dir>: real samples are never loaded in --fake mode")
    if a.stand_in and not a.fake:
        raise SystemExit("--stand-in is for --fake dry runs only")
    wants_vlm = a.part in ("vlm", "all")
    if not a.fake and a.vlm_base_url:
        raise SystemExit("real runs do not take --vlm-base-url: the harness launches llama-server on loopback from "
                         "--vlm-artefacts so the VRAM baseline precedes the model load (spec §5.6)")
    if a.fake and wants_vlm:
        if not a.vlm_base_url:
            raise SystemExit("--fake --part vlm needs --vlm-base-url (the fake endpoint)")
        V._check_endpoint(a.vlm_base_url)
    if not a.fake and wants_vlm and not a.vlm_artefacts:
        raise SystemExit("the VLM part needs --vlm-artefacts <dir> (pinned llama.cpp b11559 + GGUF)")
    if a.rerun_pair and not (a.resume and a.reason):
        raise SystemExit("--rerun-pair needs --resume and --reason")
    return a


def run(argv: list[str] | None = None) -> int:
    a = _parse(argv)
    out = Path(a.out).resolve()
    if a.decide_only:
        return decide_only(out)
    base = base_environment(a)
    if not a.fake and base.get("aidoc_dirty") is not False:
        raise SystemExit("the working tree is dirty (git status --untracked-files=no): commit first (spec §7.0 "
                         "check 2, §8.1)")
    root = Path(a.stand_in if a.fake else (a.root or samples.REAL_ROOT)).resolve()
    gq_src = root / "gt_quality.json"
    errata_src = root / "gt_errata.json"
    if not a.fake:
        for p in (gq_src, errata_src):
            if not p.is_file():
                raise SystemExit(f"{p.name} is missing in {root}: the verifier's GT preparation (plan Task 9) comes "
                                 "before any conversion (spec §6, §8.1)")
        if json.loads(gq_src.read_text(encoding="utf-8")).get("alignment_ok") is not True:
            raise SystemExit("gt_quality.json: alignment_ok is not true — fix the page mapping in MANIFEST.json "
                             "before the freeze (spec §6.2)")
        if not _commit_has_spec_and_harness(base.get("aidoc_commit") or "HEAD"):
            raise SystemExit("the commit does not hold the spec and harness (spec §7.0 check 2)")
    results = out / "results.jsonl"
    if results.exists() and not a.resume:
        raise SystemExit(f"{results} exists: use --resume or another --out")
    register = None if a.fake else Register(root / "RUNS.json", out, a.invalidated_run)   # refuses a second run
    out.mkdir(parents=True, exist_ok=True)
    log_f = open(out / "run.log", "a", encoding="utf-8")       # noqa: SIM115  closed at the end

    def log(line: str) -> None:
        stamp = datetime.now().astimezone().strftime("%H:%M:%S")
        print(f"{stamp} {line}", flush=True)
        log_f.write(f"{stamp} {line}\n")
        log_f.flush()

    try:
        manifest = _manifest(root, a.fake)
        if a.fake:
            docs, missing = samples.load(root, fake=True, manifest=root / "MANIFEST.json", stand_in=True)
        else:
            docs, missing = samples.load(root)
            if missing:                 # spec §7.0 check 1 would void the run: stop before any sample is converted
                raise SystemExit(f"samples missing or not matching the manifest: {missing}")
        errata = json.loads(errata_src.read_text(encoding="utf-8")) if errata_src.is_file() else []
        try:
            gt = {s.name: apply_errata({p: s.gt_text(p) for p in s.gt_pages}, errata, s.name)
                  for s in docs if s.group in ("A", "C")}
        except ValueError as e:
            raise SystemExit(f"gt_errata.json: {e}") from e
        bad_gt = gt_problems(docs, gt)
        gt_digest = digest_of(gt)
        if not a.fake:
            if bad_gt:                  # shared validity check gt_pages would void the run: stop before converting
                raise SystemExit(f"scored GT pages are near-empty or hold markup: {bad_gt}")
            if json.loads(gq_src.read_text(encoding="utf-8")).get("gt_sha256") != gt_digest:
                raise SystemExit("gt_quality.json gt_sha256 does not match the scoring GT (gt/ + gt_errata.json): "
                                 "the spot-check was judged on another GT")
        env_path = out / "env.json"
        prev = json.loads(env_path.read_text(encoding="utf-8")) if env_path.is_file() else None
        if prev is not None:
            for key, now in (("aidoc_commit", base.get("aidoc_commit")), ("gt_quality_sha256", _sha_text(gq_src)),
                             ("gt_errata_sha256", _sha_text(errata_src))):
                if prev.get(key) != now:
                    raise SystemExit(f"--resume across a different {key.replace('_sha256', '')} "
                                     f"({'gt_errata.json' if 'errata' in key else key}) is not the same run")
        derived = {}
        for s in docs:
            if s.group == "C":
                dst = s.scoring_pdf()
                old = ((prev or {}).get("derived") or {}).get(s.name)
                if old and dst.is_file() and samples.sha256_file(dst) == old["sha256"]:
                    derived[s.name] = old
                else:
                    derived[s.name] = samples.render_image_only(s.pdf, dst)
        staged = {s.name: _stage(s.scoring_pdf(), out / "inputs" / f"{s.name}.pdf") for s in docs}
        cover = Path(a.cover_pdf) if a.cover_pdf else (root / "cover.pdf" if a.fake else COVER_DEFAULT)
        if gq_src.is_file():
            shutil.copy2(gq_src, out / "gt_quality.json")
        head = base.get("aidoc_commit") or "HEAD"
        has = _commit_has_spec_and_harness(head)
        env = {**base, "spec_commit": _git("log", "-1", "--format=%h", "--", SPEC).stdout.strip() or None,
               "harness_commit": _git("log", "-1", "--format=%h", "--", "bench/realeval").stdout.strip() or None,
               "commit_has_spec_and_harness": has, "root": str(root), "fake": bool(a.fake),
               "samples": {s.name: s.sha256 for s in docs}, "expected_samples": len(manifest), "missing": missing,
               "derived": derived, "gt_quality_sha256": _sha_text(gq_src), "gt_errata_sha256": _sha_text(errata_src),
               "gt_sha256": gt_digest, "gt_problems": bad_gt,
               "cover_pdf": cover.name if cover else None, "cover_sha256": _sha_text(cover) if cover else None,
               "offline_declared": bool(a.offline_declared), "reruns": [], "parts": []}
        if prev is not None:
            env = {**prev, "resumed": [*prev.get("resumed", []), env.get("date") or _now()], "derived": derived,
                   "gt_problems": bad_gt, "gt_sha256": gt_digest,
                   "offline_declared": prev.get("offline_declared") and bool(a.offline_declared)}
        if a.rerun_pair:
            config, doc = a.rerun_pair.split("/", 1)
            if config not in CONFIGS or doc not in {s.name for s in docs}:
                raise SystemExit(f"--rerun-pair {a.rerun_pair}: unknown config or document")
            if register is not None:
                register.rerun(a.rerun_pair, a.reason)          # once per pair (refuses a second time)
            for name in ("results.jsonl", "pages.jsonl"):
                p = out / name
                keep = [r for r in _jsonl(p) if not (r["config"] == config and r["doc"] == doc)]
                p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in keep), encoding="utf-8")
            env["reruns"] = [*env.get("reruns", []), {"date": _now(), "pair": a.rerun_pair, "reason": a.reason}]
        if register is not None:
            register.start(base.get("aidoc_commit"))            # the first real conversion follows
        _write_json(env_path, env)
        B.write_config_files(out, CONFIGS, argparse.Namespace(vlm_base_url=None, vlm_model=None))
        if a.fake:
            _write_json(out / "fake_scenario.json", {"default": "ok", "delay_s": 0.0, "pages_per_doc": 3,
                                                     "rules": []})
        parts = PARTS if a.part == "all" else (a.part,)
        done = _done(results)
        if "scans" in parts:
            part_scans(docs, gt, staged, out, a.fake, done, log)
        if "formulas" in parts:
            part_formulas(docs, staged, out, a.fake, done, log)
        if "vlm" in parts:
            if (out / "vlm_result.json").is_file() and a.resume:
                log("vlm: vlm_result.json exists, skipped (--resume)")
            else:
                part_vlm(docs, gt, a, out, cover if cover and cover.is_file() else None, log)
        env["parts"] = [*env.get("parts", []), {"date": _now(), "parts": list(parts)}]
        _write_json(env_path, env)
        dec, val = decide_from_disk(out)
        _write_json(out / "validity.json", val)
        _write_json(out / "decisions.json", dec)
        report.write_summary(out)
        log(f"validity {val['checks']}; verdicts zh={dec['zh_scan_route']['verdict']} "
            f"formula={dec['formula_route']['verdict']} vlm={dec['vlm_local']['verdict']}")
    finally:
        log_f.close()
    return 0


if __name__ == "__main__":
    sys.exit(run())
