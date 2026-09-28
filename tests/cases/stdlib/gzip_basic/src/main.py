# gzip: in-memory compress/decompress and reading files through gzip.open /
# GzipFile, with every error path. Compressed bytes are never printed.
from __future__ import annotations
import gzip
import os
from typing import Iterator
from tpy import BinaryReadable

RAW = b"hello hello hello hello\n" * 3
# gzip.compress(RAW, mtime=0) as produced by CPython 3.12.
FIXTURE = (b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x02\x03\xcbH\xcd\xc9\xc9W"
           b"\xc8@'\xb92H\x14\x07\x00J\x0e\xce\x11H\x00\x00\x00")
# A member whose header carries every optional field (FEXTRA, FNAME,
# FCOMMENT, FHCRC), as the gzip tool and GzipFile(filename=...) write.
HEADER_FIELDS = (b"\x1f\x8b\x08\x1e\x00\x00\x00\x00\x02\xff\x03\x00abc"
                 b"name.bmp\x00a comment\x00\x00\x00\xcbHMLI-RH\xcbL\xcdI)V"
                 b"\xc8\xcf\xe6\x02\x00\xf0\xba\xd1|\x11\x00\x00\x00")


def attempt(label: str, data: bytes) -> None:
    try:
        n = len(gzip.decompress(data))
        print(label, "ok", n)
    except gzip.BadGzipFile as e:
        print(label, "BadGzipFile", str(e))
    except EOFError as e:
        print(label, "EOFError", str(e))


def write(path: str, data: bytes) -> None:
    with open(path, "wb") as f:
        f.write(data)


def load(path: str) -> bytes:
    # `with` body: the motivating read of a whole gzip file.
    with gzip.open(path) as f:
        return f.read()


def load_attempt(label: str, path: str) -> None:
    try:
        n = len(load(path))
        print(label, "ok", n)
    except gzip.BadGzipFile as e:
        print(label, "BadGzipFile", str(e))
    except EOFError as e:
        print(label, "EOFError", str(e))


def in_memory() -> None:
    # Free function: one-shot round trips and the member rules.
    print("mem fixture", gzip.decompress(FIXTURE) == RAW)
    print("mem mtime0", gzip.decompress(gzip.compress(RAW, mtime=0)) == RAW)
    print("mem mtime", gzip.decompress(gzip.compress(RAW, 1, mtime=12345.0)) == RAW)
    print("mem now", gzip.decompress(gzip.compress(RAW, 6)) == RAW)
    print("mem bytearray", gzip.decompress(bytearray(FIXTURE)) == RAW)
    print("mem multi", gzip.decompress(FIXTURE + FIXTURE) == RAW + RAW)
    print("mem padding", gzip.decompress(FIXTURE + b"\x00\x00\x00" + FIXTURE) == RAW + RAW)
    print("mem empty", gzip.decompress(b""))
    # The optional header fields are skipped.
    print("mem header fields", gzip.decompress(HEADER_FIELDS))
    big = bytes(range(256)) * 1500
    print("mem big", gzip.decompress(gzip.compress(big, mtime=0)) == big)


def in_memory_errors() -> None:
    attempt("err not gzip", b"PK\x03\x04rest")
    attempt("err one byte", b"\x1f")
    attempt("err zeros", b"\x00\x00")
    attempt("err magic only", b"\x1f\x8b")
    attempt("err truncated", FIXTURE[:len(FIXTURE) - 3])
    attempt("err header only", FIXTURE[:5])
    bad_crc = bytearray(FIXTURE)
    bad_crc[len(bad_crc) - 8] ^= 1
    attempt("err crc", bytes(bad_crc))
    bad_len = bytearray(FIXTURE)
    bad_len[len(bad_len) - 1] ^= 1
    attempt("err length", bytes(bad_len))
    attempt("err trailing", FIXTURE + b"junk")
    attempt("err method", b"\x1f\x8b\x07" + FIXTURE[3:])
    # An mtime that does not fit the header's 32-bit field.
    for stamp in [-1.0, 4294967296.0]:
        try:
            gzip.compress(RAW, mtime=stamp)
            print("err mtime", stamp, "ok")
        except Exception:
            print("err mtime", stamp, "raised")


class Loader:
    # Method: a GzipFile held in a field and read in pieces.
    f: gzip.GzipFile

    def __init__(self, path: str) -> None:
        self.f = gzip.GzipFile(path)

    def head(self) -> bytes:
        return self.f.read(5)

    def line(self) -> bytes:
        return self.f.readline()


def lines_of(path: str) -> Iterator[bytes]:
    # Generator: lines pulled from a GzipFile one at a time. A call-valued
    # manager (`gzip.open(path)`) is not lowered in a generator yet
    # (BUGS.md#with-call-manager-in-frame-unlowered).
    with gzip.GzipFile(path) as f:
        for line in f:
            yield line


def count_newlines(fp: BinaryReadable) -> int:
    n = 0
    while True:
        line = fp.readline()
        if len(line) == 0:
            return n
        n += 1


def files() -> None:
    write("gzip_basic.gz", FIXTURE)
    print("file read", load("gzip_basic.gz") == RAW)
    write("gzip_basic_fields.gz", HEADER_FIELDS)
    print("file header fields", load("gzip_basic_fields.gz"))
    # The other motivating spelling: read the raw file, decompress in memory.
    with open("gzip_basic.gz", "rb") as raw_file:
        print("file decompress", gzip.decompress(raw_file.read()) == RAW)
    loader = Loader("gzip_basic.gz")
    print("method head", loader.head(), loader.line(), loader.f.name)
    loader.f.close()
    print("generator", len([line for line in lines_of("gzip_basic.gz")]))
    with gzip.open("gzip_basic.gz") as f:
        print("readline size", f.readline(3), f.readline(-5), f.read(0))
        print("readlines", len(f.readlines()))
        print("at eof", f.read(), f.readline())
    f2 = gzip.open("gzip_basic.gz")
    print("protocol", count_newlines(f2))
    print("flags", f2.readable(), f2.writable(), f2.closed)
    f2.close()
    f2.close()
    print("closed", f2.closed, f2.name)
    try:
        f2.read()
    except ValueError as e:
        print("closed read", str(e))
    try:
        f2.readlines()
    except ValueError as e:
        print("closed readlines", str(e))
    try:
        f2.read(-2)
    except ValueError as e:
        print("closed first", str(e))
    f3 = gzip.open("gzip_basic.gz")
    try:
        f3.read(-2)
    except ValueError as e:
        print("read -2", str(e))
    f3.close()
    # Closing mid-iteration: the next line reports GzipFile's own message.
    f4 = gzip.open("gzip_basic.gz")
    try:
        for line in f4:
            print("iter line", line)
            f4.close()
    except ValueError as e:
        print("closed mid-iteration", str(e))
    # Comprehension: one gzip file per payload, read back.
    payloads = [b"a" * 10, b"b" * 100000, b""]
    for i in range(len(payloads)):
        write(f"gzip_basic_{i}.gz", gzip.compress(payloads[i], mtime=0))
    print("comprehension", [len(load(f"gzip_basic_{i}.gz")) for i in range(len(payloads))])
    # A payload spanning many read buffers, in several members with padding.
    big = bytes(range(256)) * 3000
    one = gzip.compress(big, 1, mtime=0)
    write("gzip_basic_big.gz", one + b"\x00\x00" + one)
    got = load("gzip_basic_big.gz")
    print("file big", len(got), got == big + big)
    with gzip.open("gzip_basic_big.gz") as f:
        total = 0
        while True:
            piece = f.read(1000)
            if len(piece) == 0:
                break
            total += len(piece)
        print("file pieces", total)


def file_errors() -> None:
    write("gzip_basic_plain.bin", b"not gzip at all")
    load_attempt("file not gzip", "gzip_basic_plain.bin")
    bad_crc = bytearray(FIXTURE)
    bad_crc[len(bad_crc) - 8] ^= 1
    write("gzip_basic_crc.gz", bytes(bad_crc))
    load_attempt("file crc", "gzip_basic_crc.gz")
    write("gzip_basic_trunc.gz", FIXTURE[:len(FIXTURE) - 3])
    load_attempt("file truncated", "gzip_basic_trunc.gz")
    write("gzip_basic_trail.gz", FIXTURE + b"junk")
    load_attempt("file trailing", "gzip_basic_trail.gz")
    write("gzip_basic_empty.gz", b"")
    load_attempt("file empty", "gzip_basic_empty.gz")
    # Opening an invalid file does not raise; the first read does.
    f = gzip.open("gzip_basic_plain.bin")
    f.close()
    print("file lazy", f.closed)
    try:
        gzip.open("gzip_basic_missing.gz")
    except FileNotFoundError as e:
        print("file missing", str(e))
    os.mkdir("gzip_basic_dir")
    try:
        gzip.open("gzip_basic_dir")
    except IsADirectoryError as e:
        print("file directory", str(e))


def main() -> None:
    in_memory()
    in_memory_errors()
    files()
    file_errors()


main()
