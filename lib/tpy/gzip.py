# gzip -- gzip compression and reading gzip files, a pure-TPy port of
# CPython's Lib/gzip.py over the `zlib` module.
#
# Surface: compress / decompress, open (read side), GzipFile (read side),
# BadGzipFile. The layering is CPython's: a GzipFile reads through an
# io.BufferedReader over a `_GzipReader` raw source, which parses member
# headers, inflates raw deflate data and checks each member's CRC-32 and
# length trailer. One header parser serves both the in-memory
# `decompress` (over an io.BytesIO) and the file reader.
#
# Divergences from CPython, all loud (compile errors):
#   - Only the read side of GzipFile: no write/append modes, no text modes,
#     no `fileobj=`, and no seek / tell / peek / read1 / fileno / mtime.
#   - `filename` is a `str` (no bytes or PathLike paths).
#   - `compress` raises OverflowError for an `mtime` outside [0, 2**32)
#     (CPython: struct.error, which TPy has no class for).
#   - Iterating a closed GzipFile raises at the first `next()`, not at
#     `iter()` (BUGS.md#file-iter-closed-check-deferred).
# tpy: cpp_namespace("tpystd::gzip")
import errno
import os
import time
from typing import Final, Iterator, Protocol
from tpy import int32, int64, uint32, uint8, nocopy, Own, BinaryReadable, Closable
from io import BufferedReader, BytesIO, FileIO
import zlib


FTEXT: Final[int32] = 1
FHCRC: Final[int32] = 2
FEXTRA: Final[int32] = 4
FNAME: Final[int32] = 8
FCOMMENT: Final[int32] = 16

_COMPRESS_LEVEL_FAST: Final[int32] = 1
_COMPRESS_LEVEL_BEST: Final[int32] = 9

# Compressed bytes pulled from the file per raw read, and the decompressed
# chunk size the buffered layer asks for (CPython's READ_BUFFER_SIZE).
READ_BUFFER_SIZE: Final[int32] = 128 * 1024

_TRUNCATED: Final[str] = (
    "Compressed file ended before the end-of-stream marker was reached")


class BadGzipFile(OSError):
    """Exception raised in some cases for invalid gzip files."""
    pass


class _ByteSource(Protocol):
    def read(self, size: int32 = -1) -> bytes: ...


def _u16le(b: bytes, off: int32) -> int32:
    return int32(b[off]) | (int32(b[off + 1]) << 8)


def _u32le(b: bytes, off: int32) -> uint32:
    return (uint32(b[off]) | (uint32(b[off + 1]) << 8)
            | (uint32(b[off + 2]) << 16) | (uint32(b[off + 3]) << 24))


def _read_exact(fp: _ByteSource, n: int32) -> bytes:
    data = fp.read(n)
    while len(data) < n:
        more = fp.read(n - len(data))
        if len(more) == 0:
            raise EOFError(_TRUNCATED)
        data = data + more
    return data


def _skip_cstring(fp: _ByteSource) -> None:
    while True:
        s = fp.read(1)
        if len(s) == 0 or s[0] == 0:
            return


def _read_gzip_header(fp: _ByteSource) -> bool:
    """Consume one member header; False when the source is already at its
    end. Validates the magic and the compression method like CPython."""
    magic = fp.read(2)
    if len(magic) == 0:
        return False
    if magic != b"\x1f\x8b":
        raise BadGzipFile(f"Not a gzipped file ({magic!r})")
    head = _read_exact(fp, 8)
    if head[0] != 8:
        raise BadGzipFile("Unknown compression method")
    flag = int32(head[1])
    if flag & FEXTRA:
        extra_len = _u16le(_read_exact(fp, 2), 0)
        _read_exact(fp, extra_len)
    if flag & FNAME:
        _skip_cstring(fp)
    if flag & FCOMMENT:
        _skip_cstring(fp)
    if flag & FHCRC:
        _read_exact(fp, 2)
    return True


@nocopy
class _PaddedFile:
    """A file wrapper that can push bytes back in front of the file, so the
    header parser and trailer check see input the decompressor read past."""
    _file: FileIO
    _buffer: bytes
    _read: int32

    def __init__(self, f: Own[FileIO]) -> None:
        self._file = f
        self._buffer = b""
        # -1: the pushed-back buffer is used up; reads go to the file.
        self._read = -1

    def read(self, size: int32 = -1) -> bytes:
        if self._read < 0:
            return self._file.read(size)
        start = self._read
        if size <= len(self._buffer) - start:
            self._read = start + size
            return bytes(self._buffer[start:self._read])
        self._read = -1
        head = bytes(self._buffer[start:])
        return head + self._file.read(size - len(head))

    def prepend(self, data: bytes) -> None:
        if self._read < 0:
            self._buffer = bytes(data)
        else:
            # Only ever re-prepends bytes this buffer just handed out.
            self._read -= len(data)
            return
        self._read = 0

    def close(self) -> None:
        self._file.close()


def _raw_decompressor() -> Own[zlib._Decompress]:
    # A same-module helper: assigning `zlib.decompressobj(...)` to a field
    # in a method is not lowered yet
    # (BUGS.md#field-assign-qualified-own-call-unlowered).
    return zlib.decompressobj(-zlib.MAX_WBITS)


@nocopy
class _GzipReader:
    """The raw decompressed-byte source under GzipFile's BufferedReader: a
    `RawBinaryIO`. Walks the members of the file, checking each trailer."""
    _fp: _PaddedFile
    _decompressor: zlib._Decompress
    _new_member: bool
    _crc: uint32
    _stream_size: int64

    def __init__(self, fd: int64) -> None:
        self._fp = _PaddedFile(FileIO(fd))
        self._decompressor = _raw_decompressor()
        self._new_member = True
        self._crc = 0
        self._stream_size = 0

    def read(self, size: int32 = -1) -> bytes:
        if size < 0:
            return self._readall()
        if size == 0:
            return b""
        # A single decompress() may yield no output for some inputs; retry
        # until there is data or the file ends.
        uncompress = b""
        while True:
            if self._decompressor.eof:
                self._read_eof()
                self._new_member = True
                self._decompressor = _raw_decompressor()
            if self._new_member:
                self._crc = 0
                self._stream_size = 0
                # A field passed to a protocol parameter is not lowered yet
                # (BUGS.md#field-arg-to-protocol-param-unlowered).
                fp = self._fp
                if not _read_gzip_header(fp):
                    return b""
                self._new_member = False
            buf = self._fp.read(READ_BUFFER_SIZE)
            uncompress = self._decompressor.decompress(buf, size)
            tail = self._decompressor.unconsumed_tail
            if len(tail) > 0:
                self._fp.prepend(tail)
            else:
                # Push back what the stream end read past, so the trailer
                # check and the next header see it.
                unused = self._decompressor.unused_data
                if len(unused) > 0:
                    self._fp.prepend(unused)
            if len(uncompress) > 0:
                break
            if len(buf) == 0:
                raise EOFError(_TRUNCATED)
        self._crc = zlib.crc32(uncompress, self._crc)
        self._stream_size += int64(len(uncompress))
        return uncompress

    def _readall(self) -> bytes:
        chunks: list[bytes] = []
        while True:
            data = self.read(READ_BUFFER_SIZE)
            if len(data) == 0:
                break
            chunks.append(data)
        return b"".join(chunks)

    def _read_eof(self) -> None:
        # The trailer stores the member's CRC-32 and its length mod 2**32.
        # (BUGS.md#field-arg-to-protocol-param-unlowered: alias the field.)
        fp = self._fp
        trailer = _read_exact(fp, 8)
        crc = _u32le(trailer, 0)
        isize = _u32le(trailer, 4)
        if crc != self._crc:
            raise BadGzipFile(f"CRC check failed {hex(crc)} != {hex(self._crc)}")
        if isize != uint32.trunc(self._stream_size & 0xffffffff):
            raise BadGzipFile("Incorrect length of data produced")
        # Members may be followed by zero padding.
        c = b"\x00"
        while c == b"\x00":
            c = self._fp.read(1)
        if len(c) > 0:
            self._fp.prepend(c)

    def close(self) -> None:
        self._fp.close()


def _open_for_read(filename: str) -> int64:
    fd = os.open(filename, os.O_RDONLY)
    # open(2) succeeds on a directory; CPython's open() reports it here.
    if (os.fstat(fd).st_mode & 0o170000) == 0o040000:
        os.close(fd)
        raise IsADirectoryError(errno.EISDIR, os.strerror(errno.EISDIR),
                                filename)
    return fd


@nocopy
class GzipFile(BinaryReadable, Closable):
    """A binary file object over a gzip file (read side). Closes its file
    when closed, on leaving a `with` block, or when dropped."""
    _buffer: BufferedReader
    name: str
    _closed: bool

    def __init__(self, filename: str) -> None:
        self._buffer = BufferedReader(_GzipReader(_open_for_read(filename)),
                                      READ_BUFFER_SIZE)
        self.name = filename
        self._closed = False

    def _check_not_closed(self) -> None:
        if self._closed:
            raise ValueError("I/O operation on closed file")

    def _check_closed_iobase(self) -> None:
        # CPython's IOBase spelling of the check (readlines, iteration,
        # __enter__) ends with a period; GzipFile's own does not.
        if self._closed:
            raise ValueError("I/O operation on closed file.")

    def read(self, size: int32 = -1) -> bytes:
        self._check_not_closed()
        return self._buffer.read(size)

    def readline(self, size: int32 = -1) -> bytes:
        self._check_not_closed()
        return self._buffer.readline(size)

    def readlines(self) -> Own[list[bytes]]:
        self._check_closed_iobase()
        return self._buffer.readlines()

    def __iter__(self) -> Iterator[bytes]:
        self._check_closed_iobase()
        while True:
            # Through GzipFile.readline, so a close mid-iteration reports
            # GzipFile's own closed-file message, as CPython's __next__ does.
            line = self.readline()
            if len(line) == 0:
                return
            yield line

    def readable(self) -> bool:
        return True

    def writable(self) -> bool:
        return False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._buffer.close()

    @property
    def closed(self) -> bool:
        return self._closed

    def __enter__(self) -> "GzipFile":
        self._check_closed_iobase()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


def open(filename: str) -> Own[GzipFile]:
    """Open a gzip-compressed file for reading."""
    return GzipFile(filename)


# The default is _COMPRESS_LEVEL_BEST spelled as a literal
# (BUGS.md#param-default-resolves-in-caller-scope).
def compress(data: bytes, compresslevel: int32 = 9,
             *, mtime: float | None = None) -> bytes:
    """Compress `data` into one gzip member. `mtime` defaults to now."""
    stamp = mtime if mtime is not None else time.time()
    if stamp <= -1.0 or stamp >= 4294967296.0:
        raise OverflowError("'L' format requires 0 <= number <= 4294967295")
    if stamp == 0.0:
        # zlib writes the whole member, header included, as CPython 3.12
        # delegates this case to it.
        return zlib.compress(data, compresslevel, 31)
    xfl: uint8 = 0
    if compresslevel == _COMPRESS_LEVEL_BEST:
        xfl = 2
    elif compresslevel == _COMPRESS_LEVEL_FAST:
        xfl = 4
    header = bytearray(b"\x1f\x8b\x08\x00")
    _put_u32le(header, uint32.trunc(int64(stamp)))
    header.append(xfl)
    header.append(255)
    body = zlib.compress(data, compresslevel, -zlib.MAX_WBITS)
    trailer = bytearray()
    _put_u32le(trailer, zlib.crc32(data))
    _put_u32le(trailer, uint32.trunc(int64(len(data)) & 0xffffffff))
    return bytes(header) + body + bytes(trailer)


def _put_u32le(out: bytearray, v: uint32) -> None:
    out.append(uint8.trunc(v & 0xff))
    out.append(uint8.trunc((v >> 8) & 0xff))
    out.append(uint8.trunc((v >> 16) & 0xff))
    out.append(uint8.trunc((v >> 24) & 0xff))


def decompress(data: bytes) -> bytes:
    """Decompress every gzip member in `data` (zero padding between and
    after members is allowed)."""
    members: list[bytes] = []
    rest = bytes(data)
    while True:
        fp = BytesIO(rest)
        if not _read_gzip_header(fp):
            return b"".join(members)
        do = zlib.decompressobj(-zlib.MAX_WBITS)
        decompressed = do.decompress(rest[fp.tell():])
        tail = do.unused_data
        if not do.eof or len(tail) < 8:
            raise EOFError(_TRUNCATED)
        if _u32le(tail, 0) != zlib.crc32(decompressed):
            raise BadGzipFile("CRC check failed")
        if _u32le(tail, 4) != uint32.trunc(int64(len(decompressed)) & 0xffffffff):
            raise BadGzipFile("Incorrect length of data produced")
        members.append(decompressed)
        # CPython: `unused_data[8:].lstrip(b"\x00")`; bytes.lstrip takes no
        # argument yet (BUGS.md#strip-chars-arg-missing).
        start: int32 = 8
        while start < len(tail) and tail[start] == 0:
            start += 1
        rest = bytes(tail[start:])
