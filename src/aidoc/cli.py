from __future__ import annotations
import argparse
import sys
from aidoc import __version__


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="aidoc")
    p.add_argument("--version", action="version", version=f"aidoc {__version__}")
    p.add_subparsers(dest="cmd")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd is None:
        build_parser().print_help()
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
