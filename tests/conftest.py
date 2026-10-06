import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FIXTURES = ROOT / "tests" / "fixtures"


def _gui_available() -> bool:
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return False
    try:
        import gi

        gi.require_version("Gtk", "3.0")
        gi.require_version("WebKit2", "4.1")
        from gi.repository import Gtk, WebKit2  # noqa: F401
    except (ImportError, ValueError):
        return False
    return True


GUI = _gui_available()


def pytest_collection_modifyitems(config, items):
    if GUI:
        return
    skip = pytest.mark.skip(reason="no display / GTK + WebKitGTK unavailable")
    for item in items:
        if "gui" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


def pytest_runtest_logreport(report):
    """Surface failures as GitHub annotations (readable without log access)."""
    if report.failed and os.environ.get("GITHUB_ACTIONS"):
        path, line, _ = report.location
        message = str(report.longrepr).strip().splitlines()
        tail = " | ".join(message[-6:]).replace("%", "%25").replace("\r", "").replace("\n", " ")
        print(
            f"\n::error file={path},line={(line or 0) + 1}::{report.nodeid} {report.when}: {tail[:900]}"
        )
