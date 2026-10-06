"""Command-line entry point.

slidelite [--fullscreen | --presentation] [FILE]
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass

from slidelite import APP_NAME, __version__


@dataclass(frozen=True)
class LaunchOptions:
    files: tuple[str, ...] = ()
    fullscreen: bool = False
    presentation: bool = False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="slidelite",
        description=f"{APP_NAME} - a lightweight PowerPoint (.pptx) viewer.",
    )
    parser.add_argument("files", nargs="*", metavar="FILE", help="presentation(s) to open")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--fullscreen", action="store_true", help="open the viewer fullscreen")
    mode.add_argument(
        "--presentation",
        action="store_true",
        help="start the slide show immediately",
    )
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    return parser


def parse_args(argv: list[str] | None = None) -> LaunchOptions:
    ns = build_parser().parse_args(argv)
    files = tuple(os.path.abspath(f) if "://" not in f else f for f in ns.files)
    return LaunchOptions(files=files, fullscreen=ns.fullscreen, presentation=ns.presentation)


def main(argv: list[str] | None = None) -> int:
    options = parse_args(argv)
    try:
        from slidelite.app.application import run
    except (ImportError, ValueError) as exc:  # gi missing or wrong typelib versions
        print(
            f"{APP_NAME}: GTK 3 / WebKitGTK 4.1 are required but could not be loaded ({exc}).\n"
            "Install: sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-webkit2-4.1",
            file=sys.stderr,
        )
        return 1
    return run(options)
