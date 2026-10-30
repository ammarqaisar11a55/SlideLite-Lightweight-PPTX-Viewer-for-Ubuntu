"""Locating the live objects: Current User → user edits → persist directory.

PowerPoint appends each fast save to the stream, so the newest user edit wins
and older edits only fill in the objects it did not touch.
"""

from __future__ import annotations

import struct

from slidelite.legacy.records import (
    DOCUMENT,
    PERSIST_DIRECTORY_ATOM,
    USER_EDIT_ATOM,
    Record,
    read_header,
)

ENCRYPTED_TOKEN = 0xF3D1C4DF
MAX_EDITS = 8192


class LegacyParseError(Exception):
    """The binary structure is unusable; the message is for logs."""


class LegacyEncryptedError(LegacyParseError):
    pass


def read_persist_map(current_user: bytes, stream: bytes) -> tuple[dict[int, int], int]:
    """(persist id → stream offset, document persist id)."""
    if len(current_user) < 24:
        raise LegacyParseError("Current User stream is too short")
    token, offset = struct.unpack_from("<II", current_user, 12)
    if token == ENCRYPTED_TOKEN:
        raise LegacyEncryptedError("presentation is encrypted")

    persist: dict[int, int] = {}
    document_ref = 0
    seen: set[int] = set()
    while offset not in seen and len(seen) < MAX_EDITS:
        seen.add(offset)
        edit = read_header(stream, offset)
        if edit is None or edit.type != USER_EDIT_ATOM or edit.end - edit.start < 24:
            if not persist:
                raise LegacyParseError(f"no user edit at {offset}")
            break
        previous, directory_at, doc_ref = struct.unpack_from("<III", stream, edit.start + 8)
        if not document_ref:
            document_ref = doc_ref
        directory = read_header(stream, directory_at)
        if directory is not None and directory.type == PERSIST_DIRECTORY_ATOM:
            _merge_directory(directory, persist)
        if previous == 0:
            break
        offset = previous
    if not persist or not document_ref:
        raise LegacyParseError("no persist directory")
    return persist, document_ref


def _merge_directory(directory: Record, persist: dict[int, int]) -> None:
    data = directory.data
    at = 0
    while at + 4 <= len(data):
        word = struct.unpack_from("<I", data, at)[0]
        at += 4
        first, count = word & 0xFFFFF, word >> 20
        for i in range(count):
            if at + 4 > len(data):
                return
            persist.setdefault(first + i, struct.unpack_from("<I", data, at)[0])
            at += 4


def resolve(stream: bytes, persist: dict[int, int], ref: int) -> Record | None:
    offset = persist.get(ref)
    return None if offset is None else read_header(stream, offset)


def load_document(stream: bytes, persist: dict[int, int], ref: int) -> Record:
    record = resolve(stream, persist, ref)
    if record is None or record.type != DOCUMENT:
        raise LegacyParseError("document container not found")
    return record
