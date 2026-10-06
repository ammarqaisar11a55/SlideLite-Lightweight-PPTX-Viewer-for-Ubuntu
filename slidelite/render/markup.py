"""Small helpers for producing safe markup."""

from __future__ import annotations

import html
import math

ID_TOKEN = "@@"  # noqa: S105 - replaced client-side by a unique prefix per mounted copy


def esc(text: str) -> str:
    """Escape text for HTML content/attributes (``@`` is reserved for ids)."""
    return html.escape(text, quote=True).replace("@", "&#64;")


def num(value: float, digits: int = 2) -> str:
    if not math.isfinite(value):
        return "0"
    text = f"{value:.{digits}f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def px(value: float) -> str:
    return f"{num(value)}px"


def style(**props: str | None) -> str:
    parts = [f"{k.replace('_', '-')}:{v}" for k, v in props.items() if v not in (None, "")]
    return ";".join(parts)


class IdGen:
    def __init__(self, prefix: str = "") -> None:
        self.prefix = prefix
        self.n = 0

    def __call__(self, kind: str = "d") -> str:
        self.n += 1
        return f"{ID_TOKEN}{self.prefix}{kind}{self.n}"
