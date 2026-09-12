# io -- in-memory text/binary buffers and the IO protocol surface.
#
# v1 surface: StringIO, BytesIO, plus the IO protocols from tpy core
# (Readable / Writable / BinaryReadable / BinaryWritable / Seekable /
# Closable) which both buffer types explicitly conform to. Users can
# write `def f(fp: io.Writable): ...` etc. SEEK_SET/CUR/END are exposed
# (bound to the os runtime's seek-constant globals via native_global).
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
#   - read(size) counts bytes, not codepoints (TPy-wide str-indexing
#     divergence; matches CPython for ASCII).
#   - close() drops the buffer; CPython matches.
#   - getvalue() does not collapse chunks (avoids unsolicited mutation).
#
# tpy: cpp_namespace("tpystd::io")
from typing import Final, Iterator, Protocol
from tpy import (
    int32, int64, Own, nocopy, dynamic,
    Writable, Readable, BinaryWritable, BinaryReadable,
    Seekable, Closable,
)
from tpy.extern import native_global
from tplib.box import Box
import os


# CPython's io.DEFAULT_BUFFER_SIZE: chunk size for raw reads / the default
# BufferedReader buffer.
DEFAULT_BUFFER_SIZE: Final[int32] = 8192


# SEEK_SET/CUR/END names are <cstdio> macros, so they can't be emitted as C++
# symbols; bind via native_global to the os runtime's int32 seek globals (same
# POSIX 0/1/2, int32 to match io.seek's whence; always linked).
SEEK_SET: Final[int32] = native_global("tpy::stdlib::os::kc_seek_set32")
SEEK_CUR: Final[int32] = native_global("tpy::stdlib::os::kc_seek_cur32")
SEEK_END: Final[int32] = native_global("tpy::stdlib::os::kc_seek_end32")
_SEEK_SET: int32 = 0
_SEEK_CUR: int32 = 1
_SEEK_END: int32 = 2


@nocopy
class StringIO(Writable, Readable, Seekable, Closable):
    _chunks: list[str]
    _pos: int32
    _total: int32
    _closed: bool

    def __init__(self, initial: str = "") -> None:
        self._chunks = []
        if len(initial) > 0:
            self._chunks.append(str(initial))
        self._pos = 0
        self._total = int32(len(initial))
        self._closed = False

    def write(self, s: str) -> int32:
        self._check_open()
        n: int32 = int32(len(s))
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
        end: int32 = self._pos + n
        prefix: str = buf[:self._pos]
        if end < int32(len(buf)):
            tail: str = buf[end:]
            new_buf: str = prefix + owned + tail
        else:
            new_buf = prefix + owned
        self._chunks = [new_buf]
        self._total = int32(len(new_buf))
        self._pos = end
        return n

    def read(self, size: int32 = -1) -> str:
        self._check_open()
        if self._pos >= self._total or size == 0:
            return ""
        self._collapse()
        # Byte-indexed slice (codepoint vs byte divergence for non-ASCII is
        # TPy-wide str indexing, not specific to read). `_total - _pos` instead
        # of `_pos + size` avoids int32 overflow when both are large.
        if size < 0 or size >= self._total - self._pos:
            out: str = self._chunks[0][self._pos:]
            self._pos = self._total
        else:
            stop: int32 = self._pos + size
            out = self._chunks[0][self._pos:stop]
            self._pos = stop
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
        rel: int32 = suffix.find("\n")
        if rel < 0:
            out: str = suffix
            self._pos = self._total
        else:
            stop: int32 = self._pos + rel + 1
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

    def tell(self) -> int32:
        self._check_open()
        return self._pos

    def seek(self, pos: int32, whence: int32 = 0) -> int32:
        self._check_open()
        new_pos: int32 = 0
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

    def truncate(self, size: int32 = -1) -> int32:
        self._check_open()
        n: int32 = size if size >= 0 else self._pos
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
    _pos: int32
    _total: int32
    _closed: bool

    def __init__(self, initial: bytes | None = None) -> None:
        self._chunks = []
        self._pos = 0
        self._total = 0
        if initial is not None and len(initial) > 0:
            self._chunks.append(bytes(initial))
            self._total = int32(len(initial))
        self._closed = False

    def write(self, data: bytes) -> int32:
        self._check_open()
        n: int32 = int32(len(data))
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
        end: int32 = self._pos + n
        # bytes slicing: basic_slice returns BytesView; concat needs bytes.
        # Wrap each slice with bytes(...) so '+' resolves to bytes+bytes.
        prefix: bytes = bytes(buf[:self._pos])
        if end < int32(len(buf)):
            tail: bytes = bytes(buf[end:])
            new_buf: bytes = prefix + owned + tail
        else:
            new_buf = prefix + owned
        self._chunks = [new_buf]
        self._total = int32(len(new_buf))
        self._pos = end
        return n

    def read(self, size: int32 = -1) -> bytes:
        self._check_open()
        if self._pos >= self._total or size == 0:
            return b""
        self._collapse()
        # `_total - _pos` instead of `_pos + size` avoids int32 overflow.
        if size < 0 or size >= self._total - self._pos:
            out: bytes = bytes(self._chunks[0][self._pos:])
            self._pos = self._total
        else:
            stop: int32 = self._pos + size
            out = bytes(self._chunks[0][self._pos:stop])
            self._pos = stop
        return out

    def readline(self) -> bytes:
        self._check_open()
        if self._pos >= self._total:
            return b""
        self._collapse()
        buf: bytes = self._chunks[0]
        # Same start-arg gap as str.find: search the suffix and adjust.
        suffix: bytes = bytes(buf[self._pos:])
        rel: int32 = suffix.find(b"\n")
        if rel < 0:
            out: bytes = suffix
            self._pos = self._total
        else:
            stop: int32 = self._pos + rel + 1
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

    def tell(self) -> int32:
        self._check_open()
        return self._pos

    def seek(self, pos: int32, whence: int32 = 0) -> int32:
        self._check_open()
        new_pos: int32 = 0
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

    def truncate(self, size: int32 = -1) -> int32:
        self._check_open()
        n: int32 = size if size >= 0 else self._pos
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


@nocopy
class FileIO:
    """Raw unbuffered binary I/O over an OS file descriptor.

    Adopts an existing fd (from os.pipe / os.dup / socket.fileno) -- the
    raw layer under BufferedReader, mirroring CPython's io.FileIO. read(size)
    issues a single os.read (may return fewer than size bytes, like read(2));
    read(-1) drains to EOF. With closefd=True (default) close()/__del__ close
    the fd; pass closefd=False to read from an fd owned elsewhere.

    Not declared BinaryReadable: it has read()/close() but no readline (the
    raw layer never line-splits -- BufferedReader does), and BufferedReader
    holds it as a concrete field, so no protocol erasure is needed.
    """

    # -1 sentinel marks closed/moved-from so __del__ won't double-close.
    _fd: int64 = -1
    _closefd: bool
    _closed: bool
    # When the fd is a socket in timeout mode, a recv-timeout surfaces as an
    # EAGAIN/BlockingIOError from os.read; map it to TimeoutError so the
    # makefile/BufferedReader read path matches CPython's socket.timeout.
    _timeout_mode: bool

    def __init__(self, fd: int64, closefd: bool = True,
                 timeout_mode: bool = False) -> None:
        if fd < 0:
            raise ValueError("negative file descriptor")
        self._fd = fd
        self._closefd = closefd
        self._closed = False
        self._timeout_mode = timeout_mode

    def __del__(self) -> None:
        if self._closefd and self._fd >= 0:
            os.close(self._fd)
            self._fd = -1
        # Keep _closed and the fd sentinel in agreement after teardown.
        self._closed = True

    def read(self, size: int32 = -1) -> bytes:
        self._check_open()
        if size < 0:
            return self._readall()
        if size == 0:
            return b""
        return self._os_read(int64(size))

    def _readall(self) -> bytes:
        out: bytes = b""
        while True:
            chunk: bytes = self._os_read(int64(DEFAULT_BUFFER_SIZE))
            if len(chunk) == 0:
                break
            out = out + chunk
        return out

    def _os_read(self, n: int64) -> bytes:
        if not self._timeout_mode:
            return os.read(self._fd, n)
        try:
            return os.read(self._fd, n)
        except BlockingIOError:
            # SO_RCVTIMEO elapsed on a blocking socket fd -> CPython's
            # socket.timeout, i.e. TimeoutError("timed out").
            raise TimeoutError("timed out")

    def readable(self) -> bool:
        return not self._closed

    def fileno(self) -> int64:
        self._check_open()
        return self._fd

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            if self._closefd and self._fd >= 0:
                os.close(self._fd)
            self._fd = -1

    @property
    def closed(self) -> bool:
        return self._closed

    def __enter__(self) -> "FileIO":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def _check_open(self) -> None:
        if self._closed:
            raise ValueError("I/O operation on closed file")


@dynamic
class RawBinaryIO(Protocol):
    """The raw byte source under a BufferedReader: a single `read(n)` chunk
    plus `close`. A @dynamic protocol (not a concrete field) so the source can
    be an fd-backed reader *or* a userspace decrypting one without a type
    parameter leaking up through the consumers. CPython's
    BufferedReader-over-RawIOBase shape."""
    def read(self, size: int32 = -1) -> bytes: ...
    def close(self) -> None: ...


@nocopy
class BufferedReader(BinaryReadable, Closable):
    """Buffered binary reader over a raw byte source (CPython io.BufferedReader).

    Fills an owned bytes buffer from the raw source in `buffer_size` chunks;
    read()/readline() serve from it, refilling on demand. The buffer logic
    mirrors asyncio.StreamReader (minus await): read(size>=0) blocks until
    `size` bytes are buffered or EOF; read(-1) drains to EOF.
    """

    _raw: Box[RawBinaryIO]
    _buf: bytes
    _eof: bool
    _buffer_size: int32
    _closed: bool

    def __init__(self, raw: Own[RawBinaryIO],
                 buffer_size: int32 = DEFAULT_BUFFER_SIZE) -> None:
        # `_raw` is non-default-constructible, so it must be assigned before
        # any other statement (the buffer_size guard) runs.
        self._raw = Box(raw)
        self._buf = b""
        self._eof = False
        self._buffer_size = buffer_size
        self._closed = False
        if buffer_size <= 0:
            raise ValueError("buffer size must be strictly positive")

    def _fill(self) -> None:
        chunk = self._raw.read(self._buffer_size)
        if len(chunk) == 0:
            self._eof = True
        else:
            self._buf = self._buf + chunk

    def _take(self, n: int32) -> bytes:
        # Materialize the owned head before reassigning `_buf` (a slice is a
        # borrow into the old buffer).
        head = bytes(self._buf[:n])
        self._buf = bytes(self._buf[n:])
        return head

    def read(self, size: int32 = -1) -> bytes:
        self._check_open()
        if size < 0:
            while not self._eof:
                self._fill()
            return self._take(len(self._buf))
        while len(self._buf) < size and not self._eof:
            self._fill()
        take = size if size < len(self._buf) else len(self._buf)
        return self._take(take)

    def readline(self, size: int32 = -1) -> bytes:
        """Read through the next `\\n` (included) or EOF; at most `size`
        bytes when `size >= 0`. Matches CPython's BufferedReader.readline."""
        self._check_open()
        idx = self._buf.find(b"\n")
        while idx < 0 and not self._eof and (size < 0 or len(self._buf) < size):
            self._fill()
            idx = self._buf.find(b"\n")
        stop = idx + 1 if idx >= 0 else len(self._buf)
        if size >= 0 and size < stop:
            stop = size
        return self._take(stop)

    def readlines(self) -> Own[list[bytes]]:
        out: list[bytes] = []
        while True:
            line = self.readline()
            if len(line) == 0:
                break
            out.append(line)
        return out

    def __iter__(self) -> Iterator[bytes]:
        while True:
            line = self.readline()
            if len(line) == 0:
                return
            yield line

    def readable(self) -> bool:
        return not self._closed

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._raw.close()
            self._buf = b""

    @property
    def closed(self) -> bool:
        return self._closed

    def __enter__(self) -> "BufferedReader":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def _check_open(self) -> None:
        if self._closed:
            raise ValueError("I/O operation on closed file.")
