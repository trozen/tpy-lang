# io -- in-memory text/binary buffers and the IO protocol surface.
#
# v1 surface: StringIO, BytesIO, plus the IO protocols from tpy core
# (Readable / Writable / BinaryReadable / BinaryWritable / Seekable /
# Closable) which both buffer types explicitly conform to. Users can
# write `def f(fp: io.Writable): ...` etc. SEEK_SET/CUR/END constants
# are not exposed yet -- the names collide with <cstdio> macros; pass
# the integer values 0/1/2 to seek() as a workaround.
#
# Storage strategies:
#   - StringIO: list[str] chunks. Fast append-at-end; mid-buffer writes
#     and reads collapse to a single chunk first.
#   - BytesIO: list[bytes] chunks (mirrors StringIO). bytearray would
#     allow in-place splice but slice-assignment is not on the Python
#     surface today, so the chunked approach is simpler.
#
# v1 divergences from CPython, all documented:
#   - seek past end is clamped to total (CPython back-fills with NUL/0).
#   - newline= / encoding= / errors= kwargs not supported.
#   - read(size=-1) takes no arg today (returns "all remaining").
#   - close() drops the buffer; CPython matches.
#   - getvalue() does not collapse chunks (avoids unsolicited mutation).
#
# tpy: cpp_namespace("tpystd::io")
from typing import Iterator
from tpy import (
    Int32, Own, nocopy,
    Writable, Readable, BinaryWritable, BinaryReadable,
    Seekable, Closable,
)


# NOTE: io.SEEK_SET / SEEK_CUR / SEEK_END are not exposed yet because the
# names collide with <cstdio> preprocessor macros. seek(whence=0|1|2) below
# accepts the integer values directly. The public constants need either a
# `#undef` shim from codegen or namespacing on the C++ side -- followup.
_SEEK_SET: Int32 = 0
_SEEK_CUR: Int32 = 1
_SEEK_END: Int32 = 2


@nocopy
class StringIO(Writable, Readable, Seekable, Closable):
    _chunks: list[str]
    _pos: Int32
    _total: Int32
    _closed: bool

    def __init__(self, initial: str = "") -> None:
        self._chunks = []
        if len(initial) > 0:
            self._chunks.append(str(initial))
        self._pos = 0
        self._total = Int32(len(initial))
        self._closed = False

    def write(self, s: str) -> Int32:
        self._check_open()
        n: Int32 = Int32(len(s))
        if n == 0:
            return n
        # Param `s: str` arrives as std::string_view; the chunks list stores
        # owned strings, so wrap with `str(...)` to materialize.
        owned: str = str(s)
        if self._pos == self._total:
            # Fast path: append-at-end. The dominant pattern (build payload
            # then getvalue / read) stays O(1) per write here.
            self._chunks.append(owned)
            self._total = self._total + n
            self._pos = self._total
            return n
        # Slow path: mid-buffer overwrite. Collapse + splice + re-store as
        # a single chunk; subsequent appends rejoin the fast path.
        self._collapse()
        buf: str = self._chunks[0]
        end: Int32 = self._pos + n
        prefix: str = buf[:self._pos]
        if end < Int32(len(buf)):
            tail: str = buf[end:]
            new_buf: str = prefix + owned + tail
        else:
            new_buf = prefix + owned
        self._chunks = [new_buf]
        self._total = Int32(len(new_buf))
        self._pos = end
        return n

    def read(self) -> str:
        self._check_open()
        if self._pos >= self._total:
            return ""
        self._collapse()
        out: str = self._chunks[0][self._pos:]
        self._pos = self._total
        return out

    def readline(self) -> str:
        self._check_open()
        if self._pos >= self._total:
            return ""
        self._collapse()
        buf: str = self._chunks[0]
        # str.find takes no `start` arg today (per BUGS.md / surface gap):
        # search the suffix and adjust the offset back into buf.
        suffix: str = buf[self._pos:]
        rel: Int32 = suffix.find("\n")
        if rel < 0:
            out: str = suffix
            self._pos = self._total
        else:
            stop: Int32 = self._pos + rel + 1
            out = buf[self._pos:stop]
            self._pos = stop
        return out

    def readlines(self) -> Own[list[str]]:
        # Inlined readline loop instead of `for line in self:` to side-step
        # readonly auto-deduction picking the const __iter__ overload.
        out: list[str] = []
        while True:
            line: str = self.readline()
            if not line:
                break
            out.append(line)
        return out

    def __iter__(self) -> Iterator[str]:
        while True:
            line: str = self.readline()
            if not line:
                return
            yield line

    def getvalue(self) -> str:
        self._check_open()
        if len(self._chunks) == 0:
            return ""
        if len(self._chunks) == 1:
            return self._chunks[0]
        return "".join(self._chunks)

    def tell(self) -> Int32:
        self._check_open()
        return self._pos

    def seek(self, pos: Int32, whence: Int32 = 0) -> Int32:
        self._check_open()
        new_pos: Int32 = 0
        if whence == _SEEK_SET:
            new_pos = pos
        elif whence == _SEEK_CUR:
            new_pos = self._pos + pos
        elif whence == _SEEK_END:
            new_pos = self._total + pos
        else:
            raise ValueError("invalid whence")
        if new_pos < 0:
            raise ValueError("negative seek position")
        # v1: clamp seek-past-end to total. CPython would back-fill on
        # the next write; revisit when there's a real consumer.
        if new_pos > self._total:
            new_pos = self._total
        self._pos = new_pos
        return self._pos

    def truncate(self, size: Int32 = -1) -> Int32:
        self._check_open()
        n: Int32 = size if size >= 0 else self._pos
        if n >= self._total:
            return self._total
        self._collapse()
        if n == 0:
            self._chunks = []
        else:
            self._chunks = [self._chunks[0][:n]]
        self._total = n
        if self._pos > n:
            self._pos = n
        return n

    def flush(self) -> None:
        self._check_open()

    def close(self) -> None:
        self._closed = True
        self._chunks = []
        self._total = 0
        self._pos = 0

    @property
    def closed(self) -> bool:
        return self._closed

    def readable(self) -> bool:
        return not self._closed

    def writable(self) -> bool:
        return not self._closed

    def seekable(self) -> bool:
        return not self._closed

    def __enter__(self) -> "StringIO":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def _collapse(self) -> None:
        # After this, _chunks has exactly one element holding the full buffer
        # (or is empty if total is 0).
        if len(self._chunks) > 1:
            self._chunks = ["".join(self._chunks)]
        elif len(self._chunks) == 0 and self._total > 0:
            # Defensive: invariant says this can't happen, but keep readers
            # robust against hand-edited state.
            self._chunks = [""]

    def _check_open(self) -> None:
        if self._closed:
            # CPython StringIO drops the trailing period; BytesIO keeps it.
            # We match both quirks for byte-identical cpy-phase output.
            raise ValueError("I/O operation on closed file")


@nocopy
class BytesIO(BinaryWritable, BinaryReadable, Seekable, Closable):
    _chunks: list[bytes]
    _pos: Int32
    _total: Int32
    _closed: bool

    def __init__(self, initial: bytes | None = None) -> None:
        self._chunks = []
        self._pos = 0
        self._total = 0
        if initial is not None and len(initial) > 0:
            self._chunks.append(bytes(initial))
            self._total = Int32(len(initial))
        self._closed = False

    def write(self, data: bytes) -> Int32:
        self._check_open()
        n: Int32 = Int32(len(data))
        if n == 0:
            return n
        # Param `data: bytes` arrives as std::span<const uint8_t>; chunks list
        # stores owned bytes, so wrap with `bytes(...)` to materialize.
        owned: bytes = bytes(data)
        if self._pos == self._total:
            self._chunks.append(owned)
            self._total = self._total + n
            self._pos = self._total
            return n
        self._collapse()
        buf: bytes = self._chunks[0]
        end: Int32 = self._pos + n
        # bytes slicing: basic_slice returns BytesView; concat needs bytes.
        # Wrap each slice with bytes(...) so '+' resolves to bytes+bytes.
        prefix: bytes = bytes(buf[:self._pos])
        if end < Int32(len(buf)):
            tail: bytes = bytes(buf[end:])
            new_buf: bytes = prefix + owned + tail
        else:
            new_buf = prefix + owned
        self._chunks = [new_buf]
        self._total = Int32(len(new_buf))
        self._pos = end
        return n

    def read(self) -> bytes:
        self._check_open()
        if self._pos >= self._total:
            return b""
        self._collapse()
        out: bytes = bytes(self._chunks[0][self._pos:])
        self._pos = self._total
        return out

    def readline(self) -> bytes:
        self._check_open()
        if self._pos >= self._total:
            return b""
        self._collapse()
        buf: bytes = self._chunks[0]
        # Same start-arg gap as str.find: search the suffix and adjust.
        suffix: bytes = bytes(buf[self._pos:])
        rel: Int32 = suffix.find(b"\n")
        if rel < 0:
            out: bytes = suffix
            self._pos = self._total
        else:
            stop: Int32 = self._pos + rel + 1
            out = bytes(buf[self._pos:stop])
            self._pos = stop
        return out

    def readlines(self) -> Own[list[bytes]]:
        out: list[bytes] = []
        while True:
            line: bytes = self.readline()
            if len(line) == 0:
                break
            out.append(line)
        return out

    def __iter__(self) -> Iterator[bytes]:
        while True:
            line: bytes = self.readline()
            if len(line) == 0:
                return
            yield line

    def getvalue(self) -> bytes:
        self._check_open()
        if len(self._chunks) == 0:
            return b""
        if len(self._chunks) == 1:
            return self._chunks[0]
        return b"".join(self._chunks)

    def tell(self) -> Int32:
        self._check_open()
        return self._pos

    def seek(self, pos: Int32, whence: Int32 = 0) -> Int32:
        self._check_open()
        new_pos: Int32 = 0
        if whence == _SEEK_SET:
            new_pos = pos
        elif whence == _SEEK_CUR:
            new_pos = self._pos + pos
        elif whence == _SEEK_END:
            new_pos = self._total + pos
        else:
            raise ValueError("invalid whence")
        if new_pos < 0:
            raise ValueError("negative seek position")
        if new_pos > self._total:
            new_pos = self._total
        self._pos = new_pos
        return self._pos

    def truncate(self, size: Int32 = -1) -> Int32:
        self._check_open()
        n: Int32 = size if size >= 0 else self._pos
        if n >= self._total:
            return self._total
        self._collapse()
        if n == 0:
            self._chunks = []
        else:
            self._chunks = [bytes(self._chunks[0][:n])]
        self._total = n
        if self._pos > n:
            self._pos = n
        return n

    def flush(self) -> None:
        self._check_open()

    def close(self) -> None:
        self._closed = True
        self._chunks = []
        self._total = 0
        self._pos = 0

    @property
    def closed(self) -> bool:
        return self._closed

    def readable(self) -> bool:
        return not self._closed

    def writable(self) -> bool:
        return not self._closed

    def seekable(self) -> bool:
        return not self._closed

    def __enter__(self) -> "BytesIO":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def _collapse(self) -> None:
        if len(self._chunks) > 1:
            self._chunks = [b"".join(self._chunks)]
        elif len(self._chunks) == 0 and self._total > 0:
            self._chunks = [b""]

    def _check_open(self) -> None:
        if self._closed:
            raise ValueError("I/O operation on closed file.")
