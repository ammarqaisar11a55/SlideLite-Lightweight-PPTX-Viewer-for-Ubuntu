"""Recently opened presentations (local only).

Stored in ``$XDG_STATE_HOME/slidelite/recent.json``; only the file path and
the time it was last opened are kept.  Nothing is uploaded or shared.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from slidelite.settings import atomic_write_json, state_dir

MAX_ENTRIES = 20


class RecentFiles:
    def __init__(self, path: Path | None = None, limit: int = MAX_ENTRIES) -> None:
        self.path = path or state_dir() / "recent.json"
        self.limit = limit
        self.entries: list[dict] = []
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(data, list):
            for item in data:
                if (
                    isinstance(item, dict)
                    and isinstance(item.get("path"), str)
                    and os.path.isabs(item["path"])
                ):
                    opened = item.get("opened")
                    self.entries.append(
                        {
                            "path": item["path"],
                            "opened": float(opened) if isinstance(opened, (int, float)) else 0.0,
                        }
                    )
        self.entries = self.entries[: self.limit]

    def _save(self) -> None:
        try:
            atomic_write_json(self.path, self.entries)
        except OSError:
            pass

    def add(self, path: str, when: float | None = None) -> None:
        path = os.path.abspath(path)
        self.entries = [e for e in self.entries if e["path"] != path]
        self.entries.insert(0, {"path": path, "opened": when if when is not None else time.time()})
        del self.entries[self.limit :]
        self._save()

    def remove(self, path: str) -> None:
        before = len(self.entries)
        self.entries = [e for e in self.entries if e["path"] != path]
        if len(self.entries) != before:
            self._save()

    def clear(self) -> None:
        self.entries = []
        self._save()

    def listing(self) -> list[dict]:
        """Entries for the UI: name, folder, last opened time, and existence."""
        out = []
        for e in self.entries:
            p = e["path"]
            out.append(
                {
                    "path": p,
                    "name": os.path.basename(p),
                    "folder": _display_folder(os.path.dirname(p)),
                    "opened": e["opened"],
                    "exists": os.path.isfile(p),
                }
            )
        return out


def _display_folder(folder: str) -> str:
    home = os.path.expanduser("~")
    if folder == home:
        return "~"
    if folder.startswith(home + os.sep):
        return "~" + folder[len(home) :]
    return folder
