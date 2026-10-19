"""A small read-only LevelDB reader (no dependencies), enough for Chromium's Local Storage.

Reads every table (.ldb) and the write-ahead log (.log) in a database folder and returns the
newest live value per key, by sequence number. Snappy-compressed blocks are decompressed with
the pure-Python decoder below.
"""
from __future__ import annotations

import os
import struct
from pathlib import Path


# ── Snappy (raw format, as LevelDB uses it) ─────────────────────────────────

def _varint(data: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        b = data[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, pos
        shift += 7


def snappy_decompress(data: bytes) -> bytes:
    length, pos = _varint(data, 0)
    out = bytearray()
    while pos < len(data):
        tag = data[pos]
        pos += 1
        kind = tag & 3
        if kind == 0:  # literal
            n = tag >> 2
            if n >= 60:
                extra = n - 59
                n = int.from_bytes(data[pos:pos + extra], "little")
                pos += extra
            n += 1
            out += data[pos:pos + n]
            pos += n
            continue
        if kind == 1:
            n = ((tag >> 2) & 7) + 4
            offset = ((tag >> 5) << 8) | data[pos]
            pos += 1
        elif kind == 2:
            n = (tag >> 2) + 1
            offset = int.from_bytes(data[pos:pos + 2], "little")
            pos += 2
        else:
            n = (tag >> 2) + 1
            offset = int.from_bytes(data[pos:pos + 4], "little")
            pos += 4
        start = len(out) - offset
        for i in range(n):  # copies may overlap themselves
            out.append(out[start + i])
    if len(out) != length:
        raise ValueError("snappy: length mismatch")
    return bytes(out)


# ── Tables (.ldb) ───────────────────────────────────────────────────────────

def _read_block(f, offset: int, size: int) -> bytes:
    f.seek(offset)
    raw = f.read(size + 5)  # block + 1-byte compression type + 4-byte crc
    block, kind = raw[:size], raw[size]
    if kind == 1:
        return snappy_decompress(block)
    return block


def _block_entries(block: bytes):
    if len(block) < 4:
        return
    restarts = struct.unpack_from("<I", block, len(block) - 4)[0]
    end = len(block) - 4 - restarts * 4
    pos, key = 0, b""
    while pos < end:
        shared, pos = _varint(block, pos)
        unshared, pos = _varint(block, pos)
        vlen, pos = _varint(block, pos)
        key = key[:shared] + block[pos:pos + unshared]
        pos += unshared
        value = block[pos:pos + vlen]
        pos += vlen
        yield key, value


def _handle(data: bytes, pos: int = 0) -> tuple[int, int, int]:
    off, pos = _varint(data, pos)
    size, pos = _varint(data, pos)
    return off, size, pos


def read_table(path: Path):
    """Yields (user_key, sequence, is_value, value) from one .ldb file."""
    with open(path, "rb") as f:
        f.seek(-48, os.SEEK_END)
        footer = f.read(48)
        _, _, p = _handle(footer)          # metaindex
        idx_off, idx_size, _ = _handle(footer, p)
        index = _read_block(f, idx_off, idx_size)
        for _, handle in _block_entries(index):
            off, size, _ = _handle(handle)
            for ikey, value in _block_entries(_read_block(f, off, size)):
                trailer = int.from_bytes(ikey[-8:], "little")
                yield ikey[:-8], trailer >> 8, (trailer & 0xFF) == 1, value


# ── Log (.log) ──────────────────────────────────────────────────────────────

def _log_records(path: Path):
    data = path.read_bytes()
    cur = b""
    for bs in range(0, len(data), 32768):
        blk = data[bs:bs + 32768]
        i = 0
        while i + 7 <= len(blk):
            _crc, ln, typ = struct.unpack_from("<IHB", blk, i)
            i += 7
            if typ == 0 and ln == 0:
                break
            frag = blk[i:i + ln]
            i += ln
            if typ == 1:
                yield frag
            elif typ == 2:
                cur = frag
            elif typ == 3:
                cur += frag
            elif typ == 4:
                yield cur + frag
                cur = b""


def read_log(path: Path):
    """Yields (user_key, sequence, is_value, value) from a write-ahead log."""
    for rec in _log_records(path):
        if len(rec) < 12:
            continue
        seq = int.from_bytes(rec[:8], "little")
        count = int.from_bytes(rec[8:12], "little")
        pos = 12
        for n in range(count):
            kind = rec[pos]
            pos += 1
            klen, pos = _varint(rec, pos)
            key = rec[pos:pos + klen]
            pos += klen
            value = b""
            if kind == 1:
                vlen, pos = _varint(rec, pos)
                value = rec[pos:pos + vlen]
                pos += vlen
            yield key, seq + n, kind == 1, value


def read_database(folder: Path) -> dict[bytes, bytes]:
    """Newest live value for every key in the database."""
    newest: dict[bytes, tuple[int, bool, bytes]] = {}
    folder = Path(folder)
    sources = [read_table(p) for p in sorted(folder.glob("*.ldb"))] + \
              [read_table(p) for p in sorted(folder.glob("*.sst"))] + \
              [read_log(p) for p in sorted(folder.glob("*.log"))]
    for source in sources:
        try:
            for key, seq, is_value, value in source:
                if key not in newest or seq > newest[key][0]:
                    newest[key] = (seq, is_value, value)
        except (OSError, ValueError, IndexError, struct.error):
            continue  # a damaged file costs its own entries, not the whole import
    return {k: v for k, (_, live, v) in newest.items() if live}


def chromium_string(value: bytes) -> str:
    """Local Storage values: a 0x01 prefix means Latin-1, 0x00 means UTF-16LE."""
    if value[:1] == b"\x01":
        return value[1:].decode("latin-1")
    if value[:1] == b"\x00":
        return value[1:].decode("utf-16-le", errors="replace")
    return value.decode("utf-8", errors="replace")
