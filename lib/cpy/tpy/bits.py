"""CPython stub for tpy.bits. Bit manipulation primitives."""
from __future__ import annotations


def _rot(x: int, n: int, width: int, *, right: bool) -> int:
    mask = (1 << width) - 1
    n = n % width
    if right:
        return ((x >> n) | (x << (width - n))) & mask
    return ((x << n) | (x >> (width - n))) & mask


def rotl32(x: int, n: int) -> int:
    return _rot(int(x), int(n), 32, right=False)


def rotr32(x: int, n: int) -> int:
    return _rot(int(x), int(n), 32, right=True)


def rotl64(x: int, n: int) -> int:
    return _rot(int(x), int(n), 64, right=False)


def rotr64(x: int, n: int) -> int:
    return _rot(int(x), int(n), 64, right=True)


def byteswap32(x: int) -> int:
    x = int(x) & 0xFFFFFFFF
    return ((x & 0xFF) << 24) | ((x & 0xFF00) << 8) | ((x & 0xFF0000) >> 8) | ((x >> 24) & 0xFF)


def byteswap64(x: int) -> int:
    x = int(x) & 0xFFFFFFFFFFFFFFFF
    b = x.to_bytes(8, "little")
    return int.from_bytes(b, "big")
