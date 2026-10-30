"""PowerPoint 97–2003 → OPC package bytes."""

from __future__ import annotations

import struct

from slidelite.legacy import deck
from slidelite.legacy.cfb import CompoundFile, CompoundFileError
from slidelite.legacy.persist import LegacyEncryptedError, LegacyParseError
from slidelite.legacy.pptx import Writer

__all__ = ["CompoundFileError", "LegacyEncryptedError", "LegacyParseError", "convert", "is_ppt"]


def is_ppt(data: bytes) -> bool:
    """A readable compound file holding a PowerPoint document stream."""
    try:
        return CompoundFile(data).has("PowerPoint Document")
    except (CompoundFileError, struct.error, IndexError, ValueError, OverflowError):
        return False


def convert(data: bytes) -> tuple[bytes, list[str]]:
    """(.pptx bytes, problems met along the way) for a binary presentation."""
    writer = Writer(deck.read(data))
    return writer.write(), writer.issues
