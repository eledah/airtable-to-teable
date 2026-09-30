"""Unified CLI: ``airtable-teable <stage> ...`` (stdlib only)."""

from __future__ import annotations

import argparse
import sys

from . import attachments, download, export_sqlite, import_data, migrate


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="airtable-teable",
        description="Airtable -> Teable migration pipeline (download | import | attach | migrate | export)",
    )
    ap.add_argument("--version", action="store_true", help="print version and exit")
    ap.add_argument(
        "stage", nargs="?",
        choices=["download", "import", "attach", "migrate", "export"],
        help="pipeline stage; pass '<stage> --help' for stage options",
    )
    return ap


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] in (["--version"],):
        from . import __version__
        print(__version__)
        return 0
    # Peek at the stage, then dispatch to that module's main with the rest.
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("stage", nargs="?")
    known, rest = ap.parse_known_args(argv)
    dispatch = {
        "download": download.main,
        "import": import_data.main,
        "attach": attachments.main,
        "migrate": migrate.main,
        "export": export_sqlite.main,
    }
    if known.stage not in dispatch:
        build_parser().print_help()
        return 2
    return dispatch[known.stage](rest)


if __name__ == "__main__":
    sys.exit(main())
