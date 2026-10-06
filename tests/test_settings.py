import json
import os
import stat

from slidelite.recent import RecentFiles
from slidelite.settings import DEFAULTS, Settings


def test_defaults_and_persistence(tmp_path):
    path = tmp_path / "cfg" / "settings.json"
    s = Settings(path)
    assert s.as_dict() == DEFAULTS
    assert s.set("theme", "dark")
    assert s.set("zoom", 1.5)
    assert s.set("showThumbnails", False)
    again = Settings(path)
    assert (
        again.get("theme") == "dark"
        and again.get("zoom") == 1.5
        and again.get("showThumbnails") is False
    )
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_invalid_values_are_rejected(tmp_path):
    s = Settings(tmp_path / "settings.json")
    assert not s.set("theme", "neon")
    assert not s.set("zoom", 99)
    assert not s.set("zoom", True)
    assert not s.set("unknown", 1)
    assert s.get("theme") == "system"


def test_corrupt_settings_file_falls_back_to_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{not json")
    assert Settings(path).as_dict() == DEFAULTS
    path.write_text(json.dumps({"theme": "light", "zoom": "huge", "evil": "x"}))
    s = Settings(path)
    assert s.get("theme") == "light" and s.get("zoom") == "fit" and "evil" not in s.as_dict()


def test_recent_files(tmp_path):
    deck = tmp_path / "a.pptx"
    deck.write_bytes(b"x")
    r = RecentFiles(tmp_path / "recent.json", limit=3)
    for name in ("a.pptx", "b.pptx", "c.pptx", "d.pptx"):
        r.add(str(tmp_path / name), when=1.0)
    assert [e["name"] for e in r.listing()] == ["d.pptx", "c.pptx", "b.pptx"]
    r.add(str(tmp_path / "b.pptx"), when=2.0)  # re-opening moves it to the top
    assert r.listing()[0]["name"] == "b.pptx"
    r.remove(str(tmp_path / "c.pptx"))
    assert [e["name"] for e in RecentFiles(tmp_path / "recent.json").listing()] == [
        "b.pptx",
        "d.pptx",
    ]
    r.add(str(deck))
    listing = {e["name"]: e for e in r.listing()}
    assert listing["a.pptx"]["exists"] and not listing["b.pptx"]["exists"]
    r.clear()
    assert RecentFiles(tmp_path / "recent.json").listing() == []


def test_recent_ignores_garbage(tmp_path):
    path = tmp_path / "recent.json"
    path.write_text(
        json.dumps([{"path": "relative.pptx"}, "x", {"path": "/abs/ok.pptx", "opened": "bad"}])
    )
    assert [e["path"] for e in RecentFiles(path).listing()] == ["/abs/ok.pptx"]
