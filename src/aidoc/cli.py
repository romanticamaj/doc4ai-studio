from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from aidoc import __version__

ENGINE_CHOICES = ["markitdown", "docling", "mineru"]


def _add_convert_options(p: argparse.ArgumentParser) -> None:
    p.add_argument("-o", "--output", help="output root directory (default: aidoc.toml general.output_dir)")
    p.add_argument("--engine", choices=ENGINE_CHOICES, help="force one engine, no fallback")
    p.add_argument("--lang", choices=["cht", "en"], help="OCR language (default: aidoc.toml general.lang)")
    p.add_argument("--force", action="store_true", help="re-convert even when a cached result exists")
    p.add_argument("--retry-low", action="store_true", help="re-convert cached low-quality results")
    p.add_argument("--timeout", type=int, metavar="SECONDS", help="override the computed timeout")
    p.add_argument("--allow-online-audio", action="store_true",
                   help="allow audio transcription (sends audio to an online service)")
    p.add_argument("-v", "--verbose", action="store_true", help="print engine log lines to stderr")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="aidoc")
    p.add_argument("--version", action="version", version=f"aidoc {__version__}")
    sub = p.add_subparsers(dest="cmd")
    c = sub.add_parser("convert", help="convert one file")
    c.add_argument("file")
    _add_convert_options(c)
    c.add_argument("--json", action="store_true", help="print the sidecar JSON plus status")
    b = sub.add_parser("batch", help="convert every file under a directory (recursive)")
    b.add_argument("dir")
    _add_convert_options(b)
    s = sub.add_parser("setup", help="install an engine env, download models, self-check")
    s.add_argument("engine", choices=ENGINE_CHOICES + ["all"])
    return p


def options_from_args(args, cfg):
    from aidoc.models import ConvertOptions
    out = Path(args.output).resolve() if getattr(args, "output", None) else cfg.output_root()
    return ConvertOptions(output_dir=out, engine=args.engine, lang=args.lang or cfg.general.lang, force=args.force,
                          retry_low=args.retry_low, timeout_s=args.timeout,
                          allow_online_audio=bool(args.allow_online_audio or cfg.general.enable_audio),
                          mineru_tier=cfg.engines.mineru_tier, docling_ocr=cfg.engines.docling_ocr)


def open_store(cfg):
    from aidoc.store import Store
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    return Store(cfg.data_dir / "aidoc.db")


def _display_path(p: str | None) -> str:
    if not p:
        return ""
    try:
        rel = os.path.relpath(p)
        return p if rel.startswith("..") else rel
    except ValueError:            # different drive on Windows
        return p


def _emit_verbose(enabled: bool):
    def emit(kind: str, payload: dict) -> None:
        if enabled and kind == "task.log":
            print(payload.get("line", ""), file=sys.stderr, flush=True)
    return emit


def cmd_convert(args) -> int:
    from aidoc.config import load_config
    from aidoc.engines.registry import get_engines
    from aidoc.names import file_sha256
    from aidoc.output import read_sidecar
    from aidoc.pipeline import run_task

    src = Path(args.file)
    if not src.is_file():
        print(f"error: file not found: {src}", file=sys.stderr)
        return 1
    src = src.resolve()
    cfg = load_config()
    opts = options_from_args(args, cfg)
    store = open_store(cfg)
    try:
        job = store.create_job(opts, "cli")
        st = src.stat()
        tid, _ = store.create_task(job, str(src), file_sha256(src), st.st_size, st.st_mtime, opts.lang,
                                   str(opts.output_dir / src.stem))
        status = run_task(store, tid, get_engines(cfg), cfg, emit=_emit_verbose(args.verbose))
        store.refresh_job_status(job)
        task = store.get_task(tid)
    finally:
        store.close()
    quality = task.get("quality") or {}
    if args.json:
        payload = read_sidecar(Path(task["output_dir"])) if status.value in ("done", "low", "skipped") else None
        payload = dict(payload or {"source": str(src), "sha256": task["sha256"]})
        payload.update({"status": status.value, "output_dir": task["output_dir"]})
        if status.value == "failed":
            payload.update({"error_kind": task["error_kind"], "error_msg": task["error_msg"],
                            "tried": task.get("tried") or []})
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif status.value == "failed":
        print(f"failed {task['error_kind']}: {task['error_msg']}")
    else:
        score = quality.get("score")
        line = f"{status.value} {task.get('engine') or '-'} {score if score is not None else '-'} " \
               f"{_display_path(task['output_dir'])}"
        if quality.get("level") == "low" and quality.get("reasons"):
            line += f"  reasons: {', '.join(quality['reasons'])}"
        print(line)
    return 1 if status.value == "failed" else 0


def cmd_batch(args) -> int:
    from aidoc.batch import collect_inputs, manifest_rows, run_batch
    from aidoc.config import load_config
    from aidoc.engines.registry import get_engines

    d = Path(args.dir)
    if not d.is_dir():
        print(f"error: directory not found: {d}", file=sys.stderr)
        return 1
    cfg = load_config()
    opts = options_from_args(args, cfg)
    store = open_store(cfg)
    try:
        inputs = collect_inputs(d.resolve(), exclude=opts.output_dir)
        job = run_batch(store, cfg, get_engines(cfg), inputs, opts, emit=_emit_verbose(args.verbose),
                        input_root=d.resolve())
        rows = manifest_rows(store, job, input_root=d.resolve())
    finally:
        store.close()
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
        extra = f"  {r['error']}" if r.get("error") else ""
        print(f"{r['status']:8} {r.get('engine') or '-':10} {r['source']}{extra}")
    print("summary: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) +
          f"  manifest: {_display_path(str(opts.output_dir / '_manifest.jsonl'))}")
    return 2 if counts.get("failed") else 0


def cmd_setup(args) -> int:
    from aidoc.config import load_config
    from aidoc.setup_engines import setup
    ok = setup(args.engine, load_config(), lambda line: print(line, flush=True))
    return 0 if ok else 1


def _fix_stdio() -> None:
    """Never crash on characters the console code page (e.g. cp950) cannot encode."""
    for stream in (sys.stdout, sys.stderr):
        reconf = getattr(stream, "reconfigure", None)
        if reconf is None:
            continue
        try:
            if stream.isatty():
                reconf(errors="replace")
            else:
                reconf(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def main(argv: list[str] | None = None) -> int:
    _fix_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd is None:
        parser.print_help()
        return 0
    if args.cmd == "convert":
        return cmd_convert(args)
    if args.cmd == "batch":
        return cmd_batch(args)
    if args.cmd == "setup":
        return cmd_setup(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
