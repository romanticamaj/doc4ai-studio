from __future__ import annotations
import argparse
import sys

from aidoc import __version__


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="aidoc")
    p.add_argument("--version", action="version", version=f"aidoc {__version__}")
    sub = p.add_subparsers(dest="cmd")
    s = sub.add_parser("setup", help="install an engine env, download models, self-check")
    s.add_argument("engine", choices=["markitdown", "docling", "mineru", "all"])
    return p


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
    if args.cmd == "setup":
        return cmd_setup(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
