"""Compound File Binary reader: safe, read-only access to OLE2 streams.

The file is untrusted, so sector chains are bounded and cycle-checked, sizes
are validated against the file, and the directory tree is walked iteratively.
"""

from __future__ import annotations

import struct

MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
MAX_FILE_SIZE = 2 * 1024**3
MAX_DIRECTORY_ENTRIES = 65536

_ENDOFCHAIN = 0xFFFFFFFE
_FREESECT = 0xFFFFFFFF
_FATSECT = 0xFFFFFFFD
_DIFSECT = 0xFFFFFFFC
_MAX_REGULAR = 0xFFFFFFFA


class CompoundFileError(Exception):
    """The container is damaged; the message is for logs, not for users."""


class CompoundFile:
    def __init__(self, data: bytes) -> None:
        if not data.startswith(MAGIC) or len(data) < 512:
            raise CompoundFileError("not a compound file")
        if len(data) > MAX_FILE_SIZE:
            raise CompoundFileError("file too large")
        self.data = data
        self.sector_shift = struct.unpack_from("<H", data, 0x1E)[0]
        self.mini_shift = struct.unpack_from("<H", data, 0x20)[0]
        if not 7 <= self.sector_shift <= 16 or not 3 <= self.mini_shift < self.sector_shift:
            raise CompoundFileError("bad sector size")
        self.sector_size = 1 << self.sector_shift
        self.mini_size = 1 << self.mini_shift
        self.sector_count = (len(data) - 1) // self.sector_size  # sector 0 follows the header
        self.mini_cutoff = struct.unpack_from("<I", data, 0x38)[0]
        self.fat = self._read_fat()
        self.entries = self._read_directory()
        self.streams = self._index_streams()
        root = self.entries[0]
        self.mini_stream = self._chain_bytes(root[1], root[2]) if root[1] < _MAX_REGULAR else b""
        self.mini_fat = self._read_mini_fat()

    # -- structure ------------------------------------------------------------
    def _sector(self, index: int) -> bytes:
        if index >= self.sector_count:
            raise CompoundFileError(f"sector {index} is outside the file")
        start = (index + 1) << self.sector_shift
        return self.data[start : start + self.sector_size].ljust(self.sector_size, b"\0")

    def _read_fat(self) -> list[int]:
        difat = list(struct.unpack_from("<109I", self.data, 0x4C))
        next_difat, count = struct.unpack_from("<II", self.data, 0x44)
        per_sector = self.sector_size // 4
        seen: set[int] = set()
        while next_difat < _MAX_REGULAR and len(seen) <= count:
            if next_difat in seen:
                raise CompoundFileError("DIFAT loop")
            seen.add(next_difat)
            values = struct.unpack(f"<{per_sector}I", self._sector(next_difat))
            difat.extend(values[:-1])
            next_difat = values[-1]
        fat: list[int] = []
        for sector in difat:
            if sector < _MAX_REGULAR:
                fat.extend(struct.unpack(f"<{per_sector}I", self._sector(sector)))
        return fat

    def _chain(self, start: int, table: list[int]) -> list[int]:
        chain: list[int] = []
        seen: set[int] = set()
        sector = start
        while sector < _MAX_REGULAR:
            if sector in seen or sector >= len(table):
                raise CompoundFileError("broken sector chain")
            seen.add(sector)
            chain.append(sector)
            sector = table[sector]
        return chain

    def _chain_bytes(self, start: int, size: int | None = None) -> bytes:
        raw = b"".join(self._sector(s) for s in self._chain(start, self.fat))
        return raw if size is None else raw[:size]

    def _read_directory(self) -> list[tuple[str, int, int, int, int, int, int]]:
        first = struct.unpack_from("<I", self.data, 0x30)[0]
        raw = self._chain_bytes(first)
        entries = []
        for offset in range(0, min(len(raw), MAX_DIRECTORY_ENTRIES * 128), 128):
            record = raw[offset : offset + 128]
            if len(record) < 128:
                break
            name_len = struct.unpack_from("<H", record, 64)[0]
            name = record[: max(name_len - 2, 0)].decode("utf-16-le", "replace")
            kind = record[66]
            left, right, child = struct.unpack_from("<III", record, 68)
            start = struct.unpack_from("<I", record, 116)[0]
            size = struct.unpack_from("<Q", record, 120)[0]
            if self.sector_shift == 9:
                size &= 0xFFFFFFFF  # v3 files may leave junk in the high half
            entries.append((name, start, size, kind, left, right, child))
        if not entries or entries[0][3] != 5:
            raise CompoundFileError("no root entry")
        return entries

    def _index_streams(self) -> dict[str, tuple[int, int]]:
        """Top-level streams by lower-cased name (their siblings under the root)."""
        streams: dict[str, tuple[int, int]] = {}
        stack = [self.entries[0][6]]
        seen: set[int] = set()
        while stack:
            index = stack.pop()
            if index >= len(self.entries) or index in seen:
                continue
            seen.add(index)
            name, start, size, kind, left, right, _child = self.entries[index]
            stack += [left, right]
            if kind == 2:
                streams[name.lower()] = (start, size)
        return streams

    def _read_mini_fat(self) -> list[int]:
        start, count = struct.unpack_from("<II", self.data, 0x3C)
        if start >= _MAX_REGULAR or not count:
            return []
        raw = self._chain_bytes(start)
        return list(struct.unpack(f"<{len(raw) // 4}I", raw[: len(raw) // 4 * 4]))

    # -- access ---------------------------------------------------------------
    def has(self, name: str) -> bool:
        return name.lower() in self.streams

    def read(self, name: str) -> bytes:
        start, size = self.streams[name.lower()]
        if size > len(self.data):
            raise CompoundFileError(f"stream {name!r} is larger than the file")
        if size == 0:
            return b""
        if size >= self.mini_cutoff:
            return self._chain_bytes(start, size)
        parts = []
        for sector in self._chain(start, self.mini_fat):
            at = sector << self.mini_shift
            parts.append(self.mini_stream[at : at + self.mini_size])
        return b"".join(parts)[:size]
