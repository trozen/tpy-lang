# zlib: one-shot compress/decompress, checksums, the streaming objects and
# every error path, at a free function, method, generator, comprehension and
# `with` body. Compressed bytes are never printed (they vary by zlib build).
from __future__ import annotations
from typing import Iterator
import zlib
from tpy import int32

RAW = b"hello hello hello hello\n" * 3
# zlib.compress(RAW) as produced by CPython: decoding it is build-independent.
FIXTURE = b"x\x9c\xcbH\xcd\xc9\xc9W\xc8@'\xb90D\x08\x88\x03\x00\xc6\xb7\x1a/"


def report(label: str, data: bytes, wbits: int32 = 15) -> None:
    try:
        out = zlib.decompress(data, wbits)
        print(label, "ok", len(out))
    except zlib.error as e:
        print(label, "zlib.error", str(e))


def one_shot() -> None:
    # Free function: round trips over every container wbits selects.
    print("free fixture", zlib.decompress(FIXTURE) == RAW)
    print("free default", zlib.decompress(zlib.compress(RAW)) == RAW)
    print("free level1", zlib.decompress(zlib.compress(RAW, 1)) == RAW)
    print("free gzip", zlib.decompress(zlib.compress(RAW, 9, 31), 31) == RAW)
    print("free auto", zlib.decompress(zlib.compress(RAW, 9, 31), 47) == RAW)
    print("free raw", zlib.decompress(zlib.compress(RAW, 6, -15), -15) == RAW)
    print("free wbits kw", zlib.decompress(zlib.compress(RAW, wbits=-9), wbits=-9) == RAW)
    print("free bufsize", zlib.decompress(FIXTURE, 15, 1) == RAW, zlib.decompress(FIXTURE, bufsize=0) == RAW)
    # bytearray and slices bind the `bytes` parameters.
    print("free bytearray", zlib.decompress(bytearray(FIXTURE)) == RAW)
    padded = b"??" + FIXTURE + b"??"
    print("free slice", zlib.decompress(padded[2:len(padded) - 2]) == RAW)
    print("free trailing", zlib.decompress(FIXTURE + b"junk") == RAW)
    big = bytes(range(256)) * 2000
    print("free big", zlib.decompress(zlib.compress(big, 9)) == big)
    print("free empty", zlib.decompress(zlib.compress(b"")) == b"")


def checksums() -> None:
    print("crc32", zlib.crc32(b"hello"), zlib.crc32(b""), zlib.crc32(b"\xff" * 1000))
    print("crc32 chain", zlib.crc32(b"world", zlib.crc32(b"hello ")) == zlib.crc32(b"hello world"))
    print("adler32", zlib.adler32(b"hello"), zlib.adler32(b""))
    print("adler32 chain", zlib.adler32(b"world", zlib.adler32(b"hello ")))
    crc = zlib.crc32(bytearray(b"hello"))
    print("crc32 mask", crc & 0xffffffff, hex(crc))


def errors() -> None:
    report("err garbage", b"garbage!")
    report("err truncated", FIXTURE[:len(FIXTURE) - 5])
    report("err empty", b"")
    report("err bad block", b"x\x9c\xff\xff\xff\xff")
    report("err wrong container", zlib.compress(RAW, 6, -15))
    report("err bad wbits", FIXTURE, 99)
    try:
        zlib.compress(RAW, 12)
    except zlib.error as e:
        print("err level", str(e))
    try:
        zlib.compress(RAW, 6, 99)
    except zlib.error as e:
        print("err compress wbits", str(e))
    try:
        zlib.decompress(FIXTURE, 15, -1)
    except ValueError as e:
        print("err bufsize", str(e))
    try:
        zlib.decompressobj(99)
    except ValueError as e:
        print("err decompressobj", str(e))
    try:
        zlib.compressobj(6, 7)
    except ValueError as e:
        print("err compressobj", str(e))


class Unpacker:
    # Method: a decompressor held in a field, fed across calls. The annotation
    # names typeshed's `zlib._Decompress`, not exposed by CPython at runtime.
    d: zlib._Decompress
    out: bytes

    def __init__(self) -> None:
        self.d = zlib.decompressobj()
        self.out = b""

    def feed(self, chunk: bytes) -> None:
        self.out = self.out + self.d.decompress(chunk)


def method_streaming() -> None:
    u = Unpacker()
    for i in range(0, len(FIXTURE), 4):
        u.feed(FIXTURE[i:i + 4])
    print("method chunks", u.out == RAW, u.d.eof, u.d.unused_data, u.d.unconsumed_tail)


def objects() -> None:
    d = zlib.decompressobj()
    out = d.decompress(FIXTURE + b"XY")
    print("obj unused", out == RAW, d.eof, d.unused_data, d.unconsumed_tail)
    # Input sent after the end of the stream is appended to unused_data.
    print("obj after eof", d.decompress(b"more"), d.unused_data)
    d2 = zlib.decompressobj()
    head = d2.decompress(FIXTURE, 5)
    print("obj max_length", head, len(d2.unconsumed_tail) > 0, d2.eof)
    rest = d2.flush()
    print("obj flush", head + rest == RAW, d2.unconsumed_tail, d2.eof)
    print("obj flush again", d2.flush())
    try:
        d2.decompress(b"abc")
    except zlib.error as e:
        print("obj after flush", str(e), d2.unconsumed_tail)
    d3 = zlib.decompressobj()
    print("obj partial", d3.decompress(FIXTURE[:10]), d3.flush(), d3.eof)
    d4 = zlib.decompressobj()
    try:
        d4.decompress(b"garbage!")
    except zlib.error as e:
        print("obj error", str(e))
    try:
        d4.decompress(b"more")
    except zlib.error as e:
        print("obj error sticky", str(e))
    try:
        d4.decompress(FIXTURE, -1)
    except ValueError as e:
        print("obj max_length", str(e))
    try:
        d4.flush(0)
    except ValueError as e:
        print("obj flush length", str(e))
    c = zlib.compressobj(9)
    # Two statements, not `c.compress(x) + c.flush()`: TPy does not yet
    # sequence `+` operands left to right (BUGS.md#subexpression-right-to-left-eval).
    packed = c.compress(RAW)
    packed = packed + c.flush()
    print("cobj roundtrip", zlib.decompress(packed) == RAW)
    print("cobj no flush", c.flush(zlib.Z_NO_FLUSH))
    try:
        c.compress(b"x")
    except zlib.error as e:
        print("cobj after finish", str(e))
    try:
        c.flush()
    except zlib.error as e:
        print("cobj flush after finish", str(e))
    c2 = zlib.compressobj(6, zlib.DEFLATED, -15)
    part = c2.compress(RAW)
    part = part + c2.flush(zlib.Z_SYNC_FLUSH)
    part = part + c2.flush()
    print("cobj sync", zlib.decompress(part, -15) == RAW)
    c3 = zlib.compressobj()
    try:
        c3.flush(99)
    except zlib.error as e:
        print("cobj bad mode", str(e))
    # Non-default memLevel and strategy reach deflateInit2.
    c4 = zlib.compressobj(6, zlib.DEFLATED, 15, 9, zlib.Z_RLE)
    rle = c4.compress(RAW)
    rle = rle + c4.flush()
    print("cobj rle", zlib.decompress(rle) == RAW)
    # After the finishing flush, errors carry zlib's last message.
    c5 = zlib.compressobj()
    full = c5.compress(b"abc")
    full = full + c5.flush(zlib.Z_FULL_FLUSH)
    full = full + c5.flush(zlib.Z_FULL_FLUSH)
    full = full + c5.flush()
    print("cobj full flush", zlib.decompress(full))
    try:
        c5.flush(zlib.Z_SYNC_FLUSH)
    except zlib.error as e:
        print("cobj sync after finish", str(e))
    # A stream ending inside a passed-back tail: the tail repeats the bytes
    # past the end, as CPython's does.
    d5 = zlib.decompressobj()
    d5.decompress(FIXTURE + b"JUNK", 5)
    rest5 = d5.decompress(d5.unconsumed_tail)
    print("obj tail at end", len(rest5), d5.unconsumed_tail, d5.unused_data, d5.eof)


def chunks_of(data: bytes, n: int32) -> Iterator[bytes]:
    # Generator: decompressing incrementally while yielding output.
    d = zlib.decompressobj()
    for i in range(0, len(data), n):
        yield d.decompress(data[i:i + n])
    yield d.flush()


def generator_body() -> None:
    got = b""
    for piece in chunks_of(FIXTURE, 3):
        got = got + piece
    print("generator", got == RAW)


def comprehension() -> None:
    # Comprehension: checksums over a list of inputs.
    words = [b"a", b"bc", b"def"]
    print("comprehension", [zlib.crc32(w) for w in words], [zlib.adler32(w) for w in words])


def with_body() -> None:
    # `with` body: compress what a file holds, then read it back.
    with open("zlib_basic.bin", "wb") as f:
        f.write(zlib.compress(RAW))
    with open("zlib_basic.bin", "rb") as f:
        print("with", zlib.decompress(f.read()) == RAW)


def constants() -> None:
    print("consts", zlib.MAX_WBITS, zlib.DEFLATED, zlib.DEF_MEM_LEVEL, zlib.DEF_BUF_SIZE)
    print("levels", zlib.Z_NO_COMPRESSION, zlib.Z_BEST_SPEED, zlib.Z_BEST_COMPRESSION, zlib.Z_DEFAULT_COMPRESSION)
    print("strategies", zlib.Z_DEFAULT_STRATEGY, zlib.Z_FILTERED, zlib.Z_HUFFMAN_ONLY, zlib.Z_RLE, zlib.Z_FIXED)
    print("flush", zlib.Z_NO_FLUSH, zlib.Z_PARTIAL_FLUSH, zlib.Z_SYNC_FLUSH, zlib.Z_FULL_FLUSH, zlib.Z_FINISH, zlib.Z_BLOCK, zlib.Z_TREES)
    print("versions", len(zlib.ZLIB_VERSION) > 0, len(zlib.ZLIB_RUNTIME_VERSION) > 0)


def main() -> None:
    one_shot()
    checksums()
    errors()
    method_streaming()
    objects()
    generator_body()
    comprehension()
    with_body()
    constants()


main()
