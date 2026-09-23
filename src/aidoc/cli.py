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
    b.add_argument("--token", help="API token of a running server (default: AIDOC_TOKEN, aidoc.toml server.token)")
    ch = sub.add_parser("chunk", help="split converted documents into RAG chunks (chunks.jsonl)")
    ch.add_argument("dir", help="output root that holds the converted document dirs")
    ch.add_argument("--max-tokens", type=int, default=800, help="chunk size limit in cl100k_base tokens")
    ch.add_argument("--doc", metavar="STEM", help="only this document dir")
    k = sub.add_parser("cancel", help="cancel a job on the running server")
    k.add_argument("job_id")
    k.add_argument("--token", help="API token of the server (default: AIDOC_TOKEN, aidoc.toml server.token)")
    sv = sub.add_parser("serve", help="run the server (Web UI + API) that owns the job queue")
    sv.add_argument("--host", help="bind address (default: aidoc.toml server.host, 127.0.0.1)")
    sv.add_argument("--port", type=int, help="port (default: aidoc.toml server.port, 8765)")
    sv.add_argument("--token", help="API token (default: AIDOC_TOKEN, aidoc.toml server.token); "
                                    "required when binding a non-loopback address")
    sv.add_argument("--no-recover", action="store_true", help="skip startup recovery")
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


def _recover(store, cfg) -> None:
    """Spec §8.4: clean up after an interrupted run before doing new work (logs to stderr only if it acted)."""
    from aidoc.recovery import recover_on_startup
    recover_on_startup(store, cfg, log=lambda line: print(f"recovery: {line}", file=sys.stderr, flush=True))


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
    from aidoc.batch import register_source
    from aidoc.config import load_config
    from aidoc.engines.registry import get_engines
    from aidoc.models import TaskStatus
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
        _recover(store, cfg)
        job = store.create_job(opts, "cli")
        tid, _ = register_source(store, job, src, opts)
        try:
            if store.get_task(tid)["status"] == TaskStatus.failed.value:      # vanished / unreadable
                status = TaskStatus.failed
            else:
                status = run_task(store, tid, get_engines(cfg), cfg, emit=_emit_verbose(args.verbose))
        finally:
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
    from aidoc.batch import collect_inputs, run_batch
    from aidoc.config import load_config
    from aidoc.engines.registry import get_engines

    d = Path(args.dir)
    if not d.is_dir():
        print(f"error: directory not found: {d}", file=sys.stderr)
        return 1
    cfg = load_config()
    opts = options_from_args(args, cfg)
    from aidoc import client as client_mod
    server = client_mod.find_server(cfg, getattr(args, "token", None))
    if server is not None:                   # spec §8.1: the server owns the queue; forward and follow
        from aidoc.batch import run_batch_via_server
        inputs = collect_inputs(d.resolve(), exclude=opts.output_dir)
        try:
            return run_batch_via_server(server, inputs, opts, lambda line: print(line, flush=True),
                                        input_root=d.resolve(), store_factory=lambda: open_store(cfg))
        except client_mod.ServerError as e:
            print(_server_error_text(e), file=sys.stderr)
            return 1
        finally:
            server.close()
    store = open_store(cfg)
    try:
        _recover(store, cfg)
        inputs = collect_inputs(d.resolve(), exclude=opts.output_dir)
        job = run_batch(store, cfg, get_engines(cfg), inputs, opts, emit=_emit_verbose(args.verbose),
                        input_root=d.resolve())
        store.refresh_job_status(job)
    finally:
        store.close()
    manifest = opts.output_dir / "_manifest.jsonl"
    rows = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines() if line.strip()]
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
        extra = f"  {r['error']}" if r.get("error") else ""
        print(f"{r['status']:8} {r.get('engine') or '-':10} {r['source']}{extra}")
    print("summary: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) +
          f"  manifest: {_display_path(str(opts.output_dir / '_manifest.jsonl'))}")
    return 2 if counts.get("failed") else 0


class ListenError(Exception):
    pass


def _run_uvicorn(app, host: str, port: int) -> None:
    import uvicorn
    # open SSE streams must not hold a Ctrl+C shutdown for long
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="info",
                                           timeout_graceful_shutdown=3))
    try:
        server.run()
    except SystemExit as e:                  # uvicorn exits (code 3) when it cannot bind
        if not server.started:
            raise ListenError(str(e)) from None
        raise


def cmd_serve(args) -> int:
    import time

    from aidoc import lockfile
    from aidoc.config import load_config
    from aidoc.server.app import build_context, create_app
    from aidoc.server.auth import is_loopback
    from aidoc.server.maintenance import Maintenance
    cfg = load_config()
    host = args.host or cfg.server.host
    port = args.port or cfg.server.port
    token = args.token or os.environ.get("AIDOC_TOKEN") or cfg.server.token or None
    if not is_loopback(host) and not token:
        print(f"refusing to bind {host} without --token", file=sys.stderr)
        return 2
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    lock = cfg.data_dir / "aidoc.lock"
    # the lock is readable by any local process: it says whether a token is needed, never the token itself
    if not lockfile.acquire_lock(lock, {"pid": os.getpid(), "started_at": time.time(), "host": host, "port": port,
                                        "auth": bool(token)}):
        other = lockfile.read_lock(lock) or {}
        print(f"another aidoc server is running on {other.get('host')}:{other.get('port')}", file=sys.stderr)
        return 3
    ctx = None
    try:
        ctx = build_context(cfg, token=token, start_workers=False)
        if not args.no_recover:
            _recover(ctx.store, cfg)
        ctx.uploads.reconcile()
        ctx.queue.start()
        m = ctx.extras["maintenance"] = Maintenance(ctx)
        m.start()
        print(f"aidoc serve: http://{host}:{port}  (data: {cfg.data_dir})", flush=True)
        try:
            _run_uvicorn(create_app(ctx), host, port)
        except ListenError:
            print(f"error: could not listen on {host}:{port} (address in use?); try --port", file=sys.stderr)
            return 4
        except KeyboardInterrupt:            # uvicorn re-raises the Ctrl+C / Ctrl+Break it handled
            pass
        print("server stopped; a running task went back to the queue and resumes on the next start",
              file=sys.stderr, flush=True)
    finally:
        if ctx is not None:
            ctx.close()
        lockfile.release_lock(lock)
    return 0


def _server_error_text(e) -> str:
    if e.status == 401:
        return ("error: the running server requires a token; pass --token or set AIDOC_TOKEN "
                "(or aidoc.toml server.token)")
    return f"error: {e} {e.body if isinstance(e.body, dict) else ''}".rstrip()


def cmd_cancel(args) -> int:
    from aidoc import client as client_mod
    from aidoc.config import load_config
    server = client_mod.find_server(load_config(), args.token)
    if server is None:
        print("no server running; use Ctrl+C in the terminal running the batch", file=sys.stderr)
        return 1
    try:
        job = server.cancel_job(args.job_id)
    except client_mod.ServerError as e:
        print(_server_error_text(e), file=sys.stderr)
        return 1
    finally:
        server.close()
    print(f"job {job['id']} {job['status']}")
    return 0


def cmd_chunk(args) -> int:
    from aidoc.chunk import _chunk_dirs, count_tokens
    d = Path(args.dir)
    if not d.is_dir():
        print(f"error: directory not found: {d}", file=sys.stderr)
        return 1
    if args.doc is not None and not (d / args.doc).is_dir():
        print(f"error: no document dir {args.doc!r} under {d}", file=sys.stderr)
        return 1
    from aidoc.chunk import TokenizerUnavailable
    try:
        path, n = _chunk_dirs(d, args.max_tokens, args.doc, count_tokens)
    except TokenizerUnavailable as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"wrote {n} chunks to {_display_path(str(path))}")
    return 0


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


def _break_as_interrupt() -> None:
    """Windows Ctrl+Break normally kills the process outright; treat it like Ctrl+C (clean shutdown)."""
    import signal
    if hasattr(signal, "SIGBREAK"):
        def handler(signum, frame):
            raise KeyboardInterrupt
        try:
            signal.signal(signal.SIGBREAK, handler)
        except ValueError:                    # not the main thread
            pass


def main(argv: list[str] | None = None) -> int:
    if argv is None:                          # the real console entry point (not a test calling main([...]))
        _break_as_interrupt()
    try:
        return _main(argv)
    except KeyboardInterrupt:
        print("\ninterrupted; the running conversion was cancelled (re-run the same command to resume)",
              file=sys.stderr, flush=True)
        return 130


def _main(argv: list[str] | None) -> int:
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
    if args.cmd == "chunk":
        return cmd_chunk(args)
    if args.cmd == "cancel":
        return cmd_cancel(args)
    if args.cmd == "serve":
        return cmd_serve(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
