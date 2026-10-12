"""Benchmark runner (spec 2026-10-05 verify §3.5). Not part of the `aidoc` package.

    uv run python bench/run.py --corpus A,B[,C] --configs auto,auto-norepair,docling,mineru,markitdown[,vlm]
                               --out bench/results/<date>-<host>/ [--resume] [--fake] [--repeat N]

Every (config, sample) is one `aidoc convert --force --json` subprocess with its own data dir
(`<out>/data-<config>`) and output root (`<out>/out-<config>`), so the user's Library is never touched; models come
from the user's models dir. Single-engine configs and `auto-norepair` run with AIDOC_BENCH_NO_REPAIR=1. Each result is
appended to `results.jsonl` as soon as it is scored (`--resume` skips pairs already there); `summary.md` is written at
the end by report.py.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from bench import metrics as M
from bench.gpu_sampler import Sampler

CONFIG_ORDER = ["auto", "auto-norepair", "docling", "mineru", "markitdown", "vlm"]
SINGLE = {"docling", "mineru", "markitdown", "vlm"}
RAW = {"raw-docling", "raw-markitdown", "raw-mineru"}
BOOK = "syn-book-1200"
BOOK_CONFIGS = {"auto", "mineru"}              # spec §3.5: the 1,200-page book runs only these
TIER_C_CONFIGS = {"auto"}
WARMUP_SAMPLE = ROOT / "bench" / "corpus" / "small" / "syn-text-en" / "syn-text-en.pdf"
FLAG_REASONS = {"page_dropped", "broken_text_layer", "garbage", "page_map_missing", "page_conversion_failed"}


@dataclass
class Sample:
    name: str
    tier: str
    pdf: Path
    gt: dict | None
    corpus: str

    @property
    def gt_level(self) -> str:
        return (self.gt or {}).get("gt_level", "none")

    @property
    def pages(self) -> int | None:
        """GT page count, else the PDF's (Tier C has no GT; the timeout and the per-page time need it)."""
        n = (self.gt or {}).get("pages")
        if n is None and self.pdf.is_file():
            import pymupdf
            try:
                with pymupdf.open(self.pdf) as d:
                    n = d.page_count
            except Exception:  # noqa: BLE001  unreadable: the conversion reports it
                n = None
        return n


def env_for_config(config: str, out: Path | None = None) -> dict[str, str]:
    """Environment overrides of one config (merged over os.environ by the caller)."""
    env: dict[str, str] = {}
    if config in SINGLE or config == "auto-norepair" or config in RAW:
        env["AIDOC_BENCH_NO_REPAIR"] = "1"
    if out is not None:
        from aidoc import paths
        env["AIDOC_DATA"] = str(Path(out) / f"data-{config}")
        env["AIDOC_MODELS"] = str(paths.models_dir())
        env["AIDOC_CONFIG"] = str(Path(out) / f"aidoc-{config}.toml")
    return env


# ---------------------------------------------------------------- samples

def collect(corpora: list[str], *, include_book: bool = True, log=print) -> tuple[list[Sample], list[dict]]:
    """(samples, missing entries)."""
    samples: list[Sample] = []
    missing: list[dict] = []
    for c in corpora:
        if c in ("A", "A-small"):
            base = ROOT / "bench" / "corpus" / ("small" if c == "A-small" else "")
            for gt_path in sorted(base.glob("syn-*/gt.json")):
                gt = json.loads(gt_path.read_text(encoding="utf-8"))
                if gt["kind"] == "verify-negative" or (gt["name"] == BOOK and not include_book):
                    continue
                pdf = gt_path.parent / f"{gt_path.parent.name}.pdf"
                if not pdf.is_file():
                    missing.append({"sample": gt_path.parent.name, "tier": "A", "why": "pdf not generated"})
                    continue
                samples.append(Sample(gt_path.parent.name, "A", pdf, gt, c))
            if not any(s.corpus == c for s in samples):
                log(f"corpus {c}: nothing found — run bench/corpus/make_synthetic.py{' --small' if c == 'A-small' else ''}")
        elif c == "B":
            manifest = json.loads((ROOT / "bench" / "corpus" / "MANIFEST.json").read_text(encoding="utf-8"))
            for e in manifest:
                if e["license"] == "research-only-no-redistribution":
                    continue
                d = ROOT / "bench" / "corpus" / "b" / e["name"]
                pdf = d / f"{e['name']}.pdf"
                if not pdf.is_file() or not (d / "gt.json").is_file():
                    missing.append({"sample": e["name"], "tier": "B", "why": "not fetched (bench/corpus/fetch.py)"})
                    continue
                samples.append(Sample(e["name"], "B", pdf, json.loads((d / "gt.json").read_text(encoding="utf-8")), c))
        elif c == "C":
            man = ROOT / "tests" / "fixtures" / "manual"
            entries = [("ortho-book", man / "originals" / "A System of Orthopaedic Medicine, 3rd Edition.pdf"),
                       ("hfad-book", man / "originals" / "Head First Android Development.pdf"),
                       ("hfad_cover_p1", man / "hfad_cover_p1.pdf"), ("hfad_garbled_mix", man / "hfad_garbled_mix.pdf")]
            for name, pdf in entries:
                if pdf.is_file():
                    samples.append(Sample(name, "C", pdf, None, c))
                else:
                    missing.append({"sample": name, "tier": "C", "why": "private sample not present"})
        else:
            raise SystemExit(f"unknown corpus {c!r} (A, A-small, B, C)")
    return samples, missing


def wanted(config: str, s: Sample) -> bool:
    if s.name == BOOK:
        return config in BOOK_CONFIGS
    if s.tier == "C":
        return config in TIER_C_CONFIGS
    return True


# ---------------------------------------------------------------- environment

def _sha_file(p: Path) -> str | None:
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError:
        return None


def _git(*args) -> str | None:
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True, cwd=ROOT, check=True,
                              timeout=20).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def environment(args, configs: list[str], corpora: list[str]) -> dict:
    from aidoc import paths
    from aidoc.config import load_config
    cfg = load_config()
    engines = {}
    for e in ("docling", "mineru", "markitdown", "vlm"):
        m = paths.ready_marker(e)
        info = {}
        if m.is_file():
            try:
                info = json.loads(m.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                info = {}
        engines[e] = {"ready": m.is_file(), "version": info.get("version"), "torch": info.get("torch"),
                      "cuda": info.get("cuda"), "uv_lock_sha256": _sha_file(paths.envs_dir() / e / "uv.lock")}
    gpu = None
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"],
                             capture_output=True, text=True, timeout=10, check=False).stdout.strip()
        if out:
            name, driver, mem = [x.strip() for x in out.splitlines()[0].split(",")]
            gpu = {"name": name, "driver": driver, "memory_total": mem}
    except (OSError, subprocess.SubprocessError, ValueError):
        gpu = None
    vlm = None
    if "vlm" in configs:
        from urllib.parse import urlparse
        try:
            sys.path.insert(0, str(paths.runner_dir()))
            from _vlm_prompt import PROMPT_VERSION  # type: ignore[import-not-found]
        except ImportError:
            PROMPT_VERSION = None
        vlm = {"backend": "openai", "endpoint_host": urlparse(args.vlm_base_url or "").hostname,
               "model": args.vlm_model, "prompt_version": PROMPT_VERSION}
    dirty = _git("status", "--porcelain", "--untracked-files=no")
    return {"date": datetime.now().astimezone().isoformat(timespec="seconds"), "host": platform.node(),
            "os": platform.platform(), "python": platform.python_version(), "gpu": gpu, "engines": engines,
            "aidoc_commit": _git("rev-parse", "--short=12", "HEAD"), "aidoc_dirty": bool(dirty),
            "main_uv_lock_sha256": _sha_file(ROOT / "uv.lock"),
            "aidoc_toml": {"mineru_tier": cfg.engines.mineru_tier, "docling_ocr": cfg.engines.docling_ocr},
            "configs": configs, "corpus": corpora, "fake": bool(args.fake), "repeat": args.repeat, "vlm": vlm,
            "no_repair_configs": [c for c in configs if "AIDOC_BENCH_NO_REPAIR" in env_for_config(c)]}


def write_config_files(out: Path, configs: list[str], args) -> None:
    """One aidoc.toml per config: the user's file with the VLM section set for the `vlm` config only."""
    from aidoc.config import config_path
    src = config_path()
    base = src.read_text(encoding="utf-8") if src.is_file() else ""
    import tomlkit
    for c in configs:
        doc = tomlkit.parse(base)
        vlm = doc.get("vlm")
        if vlm is None:
            vlm = tomlkit.table()
            doc["vlm"] = vlm
        if c == "vlm":
            vlm["backend"] = "openai"
            vlm["remote_enabled"] = True
            vlm["base_url"] = args.vlm_base_url
            vlm["model"] = args.vlm_model
            vlm["allow_no_key"] = True
            vlm["remote_daily_page_budget"] = 0
            vlm["remote_max_pages_per_doc"] = 2000
        else:
            vlm["backend"] = "none"
        if "server" in doc:
            doc["server"]["token"] = ""
        (out / f"aidoc-{c}.toml").write_text(tomlkit.dumps(doc), encoding="utf-8")


def _check_vlm_endpoint(url: str | None) -> None:
    """Pages never leave the machine: the vlm config accepts a loopback endpoint only."""
    import ipaddress
    from urllib.parse import urlparse
    host = urlparse(url or "").hostname or ""
    try:
        ok = ipaddress.ip_address(host).is_loopback
    except ValueError:
        ok = host == "localhost"
    if not ok:
        raise SystemExit(f"--vlm-base-url must be a loopback address (got {host!r}): pages never leave this machine")


# ---------------------------------------------------------------- one conversion

_RAW_DOCLING = """import sys
sys.path.insert(0, sys.argv[1])
from docling_runner import build_converter
c = build_converter({'ocr': 'rapidocr', 'lang': ['ch_tra', 'en']})
md = c.convert(sys.argv[2]).document.export_to_markdown(page_break_placeholder='<!-- page break -->')
open(sys.argv[3], 'w', encoding='utf-8').write(md)
"""
_RAW_MARKITDOWN = """import sys
from markitdown import MarkItDown
open(sys.argv[3], 'w', encoding='utf-8').write(MarkItDown().convert(sys.argv[2]).text_content)
"""
_RAW_MINERU = """import sys, pathlib
from mineru.parser import parse
from mineru.parser.writer import FileBasedDataWriter
out = pathlib.Path(sys.argv[3]).parent / 'mineru'
out.mkdir(parents=True, exist_ok=True)
parse(sys.argv[2], tier='basic', ocr_mode='auto').save(FileBasedDataWriter(str(out)))
pathlib.Path(sys.argv[3]).write_text((out / 'markdown.md').read_text(encoding='utf-8'), encoding='utf-8')
"""
RAW_SCRIPTS = {"raw-docling": ("docling", _RAW_DOCLING), "raw-markitdown": ("markitdown", _RAW_MARKITDOWN),
               "raw-mineru": ("mineru", _RAW_MINERU)}


def _timeout(s: Sample) -> int:
    return max(900, (s.pages or 100) * 30, 7200 if (s.pages or 0) > 400 else 0)


def convert(s: Sample, config: str, out: Path, *, fake: bool, log=print) -> dict:
    """Run one conversion; returns {status, md, sidecar, seconds, peak/baseline vram, engine_used, tried, error}."""
    from aidoc import paths
    env = {**os.environ, **env_for_config(config, out)}
    env.pop("AIDOC_TOKEN", None)
    env["PYTHONIOENCODING"] = "utf-8"
    if fake:
        env["AIDOC_FAKE_ENGINES"] = "1"
        env["AIDOC_FAKE_SCENARIO"] = str(out / "fake_scenario.json")
    root = out / f"out-{config}"
    root.mkdir(parents=True, exist_ok=True)
    if config in RAW:
        engine, script = RAW_SCRIPTS[config]
        sp = out / f"raw_{engine}.py"
        sp.write_text(script, encoding="utf-8")
        md_path = root / s.name / f"{s.name}.md"
        md_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [str(paths.venv_python(engine)), str(sp), str(paths.runner_dir()), str(s.pdf), str(md_path)]
        env["AIDOC_MODELS_DIR"] = str(paths.models_dir())
        env["MINERU_HOME"] = str(paths.models_dir() / "mineru")
        env["PYTHONUTF8"] = "1"
    else:
        cmd = [sys.executable, "-m", "aidoc.cli", "convert", str(s.pdf), "-o", str(root), "--force", "--json"]
        if config in SINGLE:
            cmd += ["--engine", config]
    t0 = time.perf_counter()
    with Sampler() as gpu:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
                               timeout=_timeout(s), check=False, cwd=str(ROOT))
            rc, stdout, stderr = r.returncode, r.stdout, r.stderr
        except subprocess.TimeoutExpired:
            rc, stdout, stderr = -1, "", f"timeout after {_timeout(s)} s"
    res = {"seconds": round(time.perf_counter() - t0, 2), "peak_vram_mb": gpu.peak, "vram_delta_mb": gpu.delta,
           "md": None, "sidecar": None, "engine_used": None, "tried": [], "error": None}
    if config in RAW:
        md_path = root / s.name / f"{s.name}.md"
        if rc == 0 and md_path.is_file():
            res.update(status="done", md=md_path.read_text(encoding="utf-8"), engine_used=config[4:])
        else:
            res.update(status="failed", error=(stderr or stdout)[-500:])
        return res
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        res.update(status="failed", error=(stderr or stdout)[-500:] or f"exit {rc}")
        return res
    st = payload.get("status")
    res["engine_used"] = payload.get("engine")
    res["tried"] = [a.get("engine") for a in payload.get("tried") or [] if isinstance(a, dict)]
    if st in ("done", "low", "skipped") and payload.get("output_dir"):
        od = Path(payload["output_dir"])
        md = od / f"{od.name}.md"
        res["md"] = md.read_text(encoding="utf-8") if md.is_file() else None
        res["sidecar"] = payload
        level = (payload.get("quality") or {}).get("level")
        res["status"] = "low" if st == "low" or level == "low" else ("warn" if level == "warn" else "done")
    else:
        res["status"] = "failed"
        res["error"] = f"{payload.get('error_kind')}: {payload.get('error_msg')}"
    return res


# ---------------------------------------------------------------- scoring

def score(s: Sample, config: str, conv: dict) -> dict:
    """Metrics of one conversion against the sample's ground truth + `aidoc verify` of the output."""
    from aidoc.pagemap import apply_inferred, infer_pages, pdf_text_layers, split_pages
    from aidoc.verify import verify_markdown
    md = conv.get("md")
    m: dict = {}
    v: dict = {"score": 0.0, "level": None, "pages_flagged": None, "flagged_pages": [], "verifiable": None}
    gt = s.gt or {}
    if md is None:                                   # failed: counted as NED 0, never skipped (spec §3.5)
        if s.gt_level == "L1":
            m.update(ned=0.0, dropped_rate=1.0)
            if gt.get("lang") == "cht":
                m["cer"] = 1.0
        return {"metrics": m, "verify": v, "gt_defects": _gt_defects(gt), "low_pages": []}
    secs: dict[int, str] = {}
    if config in RAW:
        layers = pdf_text_layers(s.pdf)
        inf = infer_pages(md, layers)
        if inf["anchored"]:
            _, secs = split_pages(apply_inferred(md, inf["sections"]))
        m["inferred_confidence"] = inf["confidence"]
    else:
        _, secs = split_pages(md)
        q = (conv.get("sidecar") or {}).get("quality") or {}
        pm = q.get("page_map") or {}
        m["coverage"] = pm.get("coverage")
        al = pm.get("alignment") or {}
        m["alignment"] = al.get("ratio") if isinstance(al, dict) else None
        m["pages_repaired"] = sum(1 for p in q.get("pages") or [] if p.get("repaired_by"))
        m["pages_unrepaired"] = q.get("pages_unrepaired")
    low_pages: list[int] = []
    unrecovered: list[int] = []
    if s.gt_level == "L1":
        if secs:
            sc = M.page_scores(gt, secs)
        else:                                        # no page map (raw output of a scan): whole-document NED
            whole = "\n".join(gt["page_text"].values())
            sc = {int(k): {"ned": M.ned(whole, M.plain(md)), "cer": M.cer(whole, M.plain(md)), "dropped": False}
                  for k in gt["page_text"]}
        m["ned"] = M.mean_ned(sc, gt)
        blank = set(gt.get("blank_pages") or [])
        content = [p for p in sc if p not in blank]
        if gt.get("lang") == "cht":
            m["cer"] = round(sum(sc[p]["cer"] for p in content) / len(content), 4) if content else 0.0
        m["dropped_rate"] = round(M.dropped_rate(gt, sc), 4)
        broken = (gt.get("defects") or {}).get("broken_text_layer_pages") or []
        if broken:
            m["recovery"] = M.recovery_rate(broken, sc)
        unrecovered = [p for p in _gt_defects(gt) if sc.get(p, {"ned": 0.0})["ned"] < M.RECOVERED_NED]
        low_pages = sorted(p for p in content if sc[p]["ned"] < 0.5)
        if gt.get("tables"):
            teds, teds_s = [], []
            for p, tables in gt["tables"].items():
                teds += M.tables_teds(tables, secs.get(int(p), ""))
                teds_s += M.tables_teds(tables, secs.get(int(p), ""), structure_only=True)
            m["teds"] = round(sum(teds) / len(teds), 4)
            m["teds_s"] = round(sum(teds_s) / len(teds_s), 4)
        if gt.get("formulas"):
            vals, n = 0.0, 0
            for p, fs in gt["formulas"].items():
                vals += M.formula_ned(fs, secs.get(int(p), "")) * len(fs)
                n += len(fs)
            m["formula_ned"] = round(vals / n, 4)
    try:
        rep = verify_markdown(md, s.pdf)
        v = {"score": rep.summary["score"], "level": rep.level, "pages_flagged": rep.summary["pages_flagged"],
             "flagged_pages": sorted(e["page"] for e in rep.pages if set(e.get("reasons") or []) & FLAG_REASONS),
             "reasons": rep.summary["reasons"], "verifiable": rep.verifiable,
             "inferred": bool(rep.page_map and rep.page_map.get("method") == "inferred")}
    except ValueError as e:
        v["error"] = str(e)
    return {"metrics": m, "verify": v, "gt_defects": _gt_defects(gt), "gt_defects_unrecovered": unrecovered,
            "low_pages": low_pages}


def _gt_defects(gt: dict) -> list[int]:
    d = gt.get("defects") or {}
    return sorted(set(d.get("broken_text_layer_pages") or []) | set(d.get("dropped_pages") or []))


# ---------------------------------------------------------------- main loop

def _done_pairs(results: Path) -> set[tuple[str, str]]:
    if not results.is_file():
        return set()
    out = set()
    for line in results.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            out.add((r["config"], r["sample"]))
    return out


def warm_up(configs: list[str], out: Path, *, fake: bool, log=print) -> dict[str, float]:
    """Convert the 3-page warm-up sample once per engine (not scored); its wall time is the startup estimate."""
    est: dict[str, float] = {}
    if not WARMUP_SAMPLE.is_file():
        return est
    engines = sorted({c for c in configs if c in SINGLE} | ({"docling", "mineru", "markitdown"}
                                                            if {"auto", "auto-norepair"} & set(configs) else set()))
    s = Sample("warmup", "A", WARMUP_SAMPLE, None, "warmup")
    for e in engines:
        if e == "vlm" and "vlm" not in configs:
            continue
        r = convert(s, e, out / "warmup", fake=fake, log=log)
        est[e] = r["seconds"]
        log(f"warm-up {e}: {r['seconds']:.1f}s ({r['status']})")
    return est


def run(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--corpus", default="A", help="A, A-small, B, C (comma-separated)")
    ap.add_argument("--configs", default="auto,auto-norepair,docling,mineru,markitdown")
    ap.add_argument("--out", required=True)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--fake", action="store_true", help="fake engines (CI plumbing), A-small only")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--no-book", action="store_true", help="leave out the 1,200-page book")
    ap.add_argument("--only", help="comma-separated sample names")
    ap.add_argument("--vlm-base-url", default="http://127.0.0.1:11434/v1")
    ap.add_argument("--vlm-model", default="gemma4:12b")
    ap.add_argument("--no-report", action="store_true")
    a = ap.parse_args(argv)
    corpora = a.corpus.split(",")
    configs = a.configs.split(",")
    unknown = [c for c in configs if c not in CONFIG_ORDER and c not in RAW]
    if unknown:
        raise SystemExit(f"unknown config(s): {unknown}")
    if a.fake:
        corpora = ["A-small"]
    if "vlm" in configs:
        _check_vlm_endpoint(a.vlm_base_url)
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    log_f = open(out / "run.log", "a", encoding="utf-8")       # noqa: SIM115  closed at the end

    def log(line: str) -> None:
        stamp = datetime.now().astimezone().strftime("%H:%M:%S")
        print(f"{stamp} {line}", flush=True)
        log_f.write(f"{stamp} {line}\n")
        log_f.flush()
    if a.fake:
        (out / "fake_scenario.json").write_text(json.dumps({"default": "ok", "delay_s": 0.0, "pages_per_doc": 3,
                                                            "rules": []}), encoding="utf-8")
    write_config_files(out, configs + (["docling", "mineru", "markitdown"] if not a.fake else []), a)
    samples, missing = collect(corpora, include_book=not a.no_book, log=log)
    if a.only:
        keep = set(a.only.split(","))
        samples = [s for s in samples if s.name in keep]
    env_path = out / "env.json"
    env = environment(a, configs, corpora)
    if env_path.is_file() and a.resume:
        prev = json.loads(env_path.read_text(encoding="utf-8"))
        env = {**prev, "resumed": prev.get("resumed", []) + [env["date"]],
               "configs": sorted(set(prev.get("configs", [])) | set(configs), key=_order),
               "corpus": sorted(set(prev.get("corpus", [])) | set(corpora)), "vlm": env["vlm"] or prev.get("vlm")}
    env["missing"] = sorted({json.dumps(m, sort_keys=True) for m in (env.get("missing") or [])} |
                            {json.dumps(m, sort_keys=True) for m in missing})
    env["missing"] = [json.loads(m) for m in env["missing"]]
    if not a.fake and "startup_estimate_s" not in env:
        env["startup_estimate_s"] = warm_up(configs, out, fake=False, log=log)
    env_path.write_text(json.dumps(env, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    results = out / "results.jsonl"
    done = _done_pairs(results) if a.resume else set()
    if not a.resume and results.exists():
        raise SystemExit(f"{results} exists: use --resume or another --out")
    startup = env.get("startup_estimate_s") or {}
    for config in configs:
        for s in samples:
            if not wanted(config, s) or (config, s.name) in done:
                continue
            for rep in range(a.repeat):
                log(f"{config} {s.name} ({s.pages or '?'} pages){f' repeat {rep + 1}' if a.repeat > 1 else ''}")
                conv = convert(s, config, out, fake=a.fake, log=log)
                sc = score(s, config, conv)
                pages = s.pages or ((conv.get("sidecar") or {}).get("pages"))
                eng = conv.get("engine_used")
                row = {"config": config, "sample": s.name, "tier": s.tier, "corpus": s.corpus, "gt_level": s.gt_level,
                       "kind": (s.gt or {}).get("kind"), "lang": (s.gt or {}).get("lang"),
                       "purpose": (s.gt or {}).get("purpose"), "status": conv["status"], "engine_used": eng,
                       "tried": conv.get("tried"), "seconds": conv["seconds"],
                       "seconds_excl_startup": (round(max(0.0, conv["seconds"] - startup[eng]), 2)
                                                if eng in startup else None),
                       "peak_vram_mb": conv.get("peak_vram_mb"), "vram_delta_mb": conv.get("vram_delta_mb"),
                       "pages": pages, "repeat": rep, **sc, "error": conv.get("error")}
                with open(results, "a", encoding="utf-8") as f:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
                mm = sc["metrics"]
                log(f"  -> {conv['status']} {eng or '-'} {conv['seconds']:.1f}s ned={mm.get('ned')} "
                    f"verify={sc['verify'].get('level')} {conv.get('error') or ''}")
    log_f.close()
    if not a.no_report:
        from bench import report
        report.write_summary(out)
    return 0


def _order(c: str) -> int:
    return CONFIG_ORDER.index(c) if c in CONFIG_ORDER else len(CONFIG_ORDER)


if __name__ == "__main__":
    sys.exit(run())
