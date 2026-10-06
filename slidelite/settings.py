"""User preferences, stored locally as JSON (never synchronised anywhere).

Location: ``$XDG_CONFIG_HOME/slidelite/settings.json`` (``~/.config``).
Writes are atomic and the file is private to the user (0600).
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

DEFAULTS: dict = {
    "theme": "system",  # system | light | dark
    "showThumbnails": True,
    "zoom": "fit",  # fit | width | a number (1.0 == 100%)
    "useTimings": True,  # honour slide auto-advance timings in the slide show
    "loop": False,  # restart the show after the last slide
}

_VALIDATORS = {
    "theme": lambda v: v in ("system", "light", "dark"),
    "showThumbnails": lambda v: isinstance(v, bool),
    "zoom": lambda v: (
        v in ("fit", "width")
        or (isinstance(v, (int, float)) and not isinstance(v, bool) and 0.1 <= v <= 4)
    ),
    "useTimings": lambda v: isinstance(v, bool),
    "loop": lambda v: isinstance(v, bool),
}


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "slidelite"


def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return Path(base) / "slidelite"


def atomic_write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class Settings:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or config_dir() / "settings.json"
        self.values = dict(DEFAULTS)
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(data, dict):
            for key, value in data.items():
                if key in _VALIDATORS and _VALIDATORS[key](value):
                    self.values[key] = value

    def get(self, key: str):
        return self.values.get(key, DEFAULTS.get(key))

    def set(self, key: str, value) -> bool:
        """Store a validated value; returns False for unknown/invalid input."""
        validator = _VALIDATORS.get(key)
        if validator is None or not validator(value):
            return False
        if self.values.get(key) == value:
            return True
        self.values[key] = value
        try:
            atomic_write_json(self.path, self.values)
        except OSError:
            pass  # preferences are a convenience; never fail the UI
        return True

    def as_dict(self) -> dict:
        return dict(self.values)
