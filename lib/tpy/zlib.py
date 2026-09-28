# zlib -- compression compatible with gzip, over the vendored zlib library.
#
# Pure-TPy facade over `_bindings.zlib` (a z_stream shim, see
# runtime/cpp/src/stdlib/zlib_shim.c). Mirrors CPython's zlibmodule.c:
# the same result/exception shapes and message texts, the same end-of-stream
# bookkeeping for `unused_data` / `unconsumed_tail`, and the same "stream
# is dead after the final flush" behavior.
#
# Surface: compress / decompress, crc32 / adler32, compressobj / _Compress,
# decompressobj / _Decompress, error, the CPython constants, ZLIB_VERSION /
# ZLIB_RUNTIME_VERSION. The stream classes carry typeshed's private names:
# CPython does not expose them at runtime, so user code builds them through
# compressobj() / decompressobj() and names them only in annotations.
#
# Divergences from CPython, all loud:
#   - `bytes` parameters take bytes, bytearray and their slices; memoryview
#     and other buffer objects are compile errors.
#   - crc32 / adler32 return uint32 (CPython: an unbounded int). `+`, `-`
#     and `<<` past [0, 2**32) panic instead of growing; `~` is the 32-bit
#     complement (CPython: a negative int).
#   - No `zdict=` and no `_Compress.copy()` / `_Decompress.copy()`.
#   - `zlib._Compress(...)` / `zlib._Decompress(...)` construct directly in
#     TPy; CPython has no such attributes (the underscore marks them private).
#   - Lengths are int32: inputs and outputs are limited to 2 GiB.
# tpy: cpp_namespace("tpystd::zlib")
from typing import Final
from tpy import Ptr, uint8, uint32, int32, uint64, readonly, nocopy, Own, take_ptr
from tpy.mem import UninitHeapStorage
from tpy.unsafe import (
    unsafe_ptr, unsafe_ptr_add, unsafe_bytes_from_buf, unsafe_str_from_cstr,
)
from _bindings import zlib as _z


MAX_WBITS: Final[int32] = 15
DEFLATED: Final[int32] = 8
DEF_MEM_LEVEL: Final[int32] = 8
DEF_BUF_SIZE: Final[int32] = 16384

Z_NO_COMPRESSION: Final[int32] = 0
Z_BEST_SPEED: Final[int32] = 1
Z_BEST_COMPRESSION: Final[int32] = 9
Z_DEFAULT_COMPRESSION: Final[int32] = -1

Z_DEFAULT_STRATEGY: Final[int32] = 0
Z_FILTERED: Final[int32] = 1
Z_HUFFMAN_ONLY: Final[int32] = 2
Z_RLE: Final[int32] = 3
Z_FIXED: Final[int32] = 4

Z_NO_FLUSH: Final[int32] = 0
Z_PARTIAL_FLUSH: Final[int32] = 1
Z_SYNC_FLUSH: Final[int32] = 2
Z_FULL_FLUSH: Final[int32] = 3
Z_FINISH: Final[int32] = 4
Z_BLOCK: Final[int32] = 5
Z_TREES: Final[int32] = 6

# Version of zlib.h the shim was built against / of the zlib linked in.
ZLIB_VERSION: str = unsafe_str_from_cstr(_z.header_version())
ZLIB_RUNTIME_VERSION: str = unsafe_str_from_cstr(_z.runtime_version())

# Public defaults are spelled as literals (Z_DEFAULT_COMPRESSION = -1,
# DEFLATED = 8, MAX_WBITS = 15, DEF_MEM_LEVEL = 8, Z_DEFAULT_STRATEGY = 0,
# Z_FINISH = 4, DEF_BUF_SIZE = 16384): a default naming a module constant
# resolves in the caller's scope (BUGS.md#param-default-resolves-in-caller-scope).

# Output chunks start at the caller's size and double up to this cap, so a
# large result costs few inflate/deflate round trips.
_MAX_CHUNK: int32 = 1 << 20


class error(Exception):
    """Raised on compression / decompression errors."""
    pass


def _error(rc: int32, zmsg: Ptr[readonly[uint8]], what: str) -> Own[error]:
    """CPython's zlib_error(): zlib's own message when it set one, else a
    fixed text per code, formatted "Error <code> <what>[: <message>]"."""
    detail = ""
    if rc == _z.VERSION_ERROR:
        detail = "library version mismatch"
    elif zmsg is not None:
        detail = unsafe_str_from_cstr(zmsg)
    elif rc == _z.BUF_ERROR:
        detail = "incomplete or truncated stream"
    elif rc == _z.STREAM_ERROR:
        detail = "inconsistent stream state"
    elif rc == _z.DATA_ERROR:
        detail = "invalid input data"
    if len(detail) == 0:
        return error(f"Error {rc} {what}")
    return error(f"Error {rc} {what}: {detail}")


def _next_chunk(cap: int32) -> int32:
    if cap >= _MAX_CHUNK:
        return _MAX_CHUNK
    return cap * 2


@nocopy
class _Stream:
    """Sole owner of one shim stream; frees it when dropped."""
    p: Ptr[_z.Stream]

    def __init__(self, p: Ptr[_z.Stream]) -> None:
        self.p = p

    def __del__(self) -> None:
        _z.free(self.p)


def _inflate_stream(wbits: int32, for_object: bool) -> Ptr[_z.Stream]:
    """An inflate stream; the init error texts differ, as in CPython, between
    the one-shot decompress() and a decompressobj()."""
    rc: int32 = 0
    p = _z.inflate_new(wbits, take_ptr(rc))
    if p is None:
        if not for_object:
            if rc == _z.MEM_ERROR:
                raise MemoryError("Out of memory while decompressing data")
            raise _error(rc, None, "while preparing to decompress data")
        if rc == _z.MEM_ERROR:
            raise MemoryError("Can't allocate memory for decompression object")
        if rc == _z.STREAM_ERROR:
            raise ValueError("Invalid initialization option")
        raise _error(rc, None, "while creating decompression object")
    return p


def _deflate_stream(level: int32, method: int32, wbits: int32,
                    memlevel: int32, strategy: int32) -> Ptr[_z.Stream]:
    """A stream for compressobj (CPython's object-init error texts)."""
    rc: int32 = 0
    p = _z.deflate_new(level, method, wbits, memlevel, strategy, take_ptr(rc))
    if p is None:
        if rc == _z.MEM_ERROR:
            raise MemoryError("Can't allocate memory for compression object")
        if rc == _z.STREAM_ERROR:
            raise ValueError("Invalid initialization option")
        raise _error(rc, None, "while creating compression object")
    return p


class _Pump:
    """The result of feeding one input buffer through a stream: the output
    chunks, the input bytes consumed, and zlib's last code."""
    parts: list[bytes]
    pos: int32
    rc: int32

    def __init__(self) -> None:
        self.parts = []
        self.pos = 0
        self.rc = _z.OK

    def output(self) -> bytes:
        return b"".join(self.parts)


def _pump(s: Ptr[_z.Stream], data: bytes, flush: int32, first_chunk: int32,
          max_length: int32) -> Own[_Pump]:
    """Run inflate/deflate over `data` until the output buffer is left
    partly empty (the input is used up or needs more), the stream ends, an
    error code comes back, or `max_length` (> 0) output bytes exist.
    Mirrors the output-buffer loop of CPython's zlibmodule.c."""
    out = _Pump()
    n = len(data)
    base = unsafe_ptr(data)
    # The first chunk size is only a hint (CPython's `bufsize`), so it is
    # capped like the later ones -- the shim fills at most 1 GiB per call.
    cap = first_chunk if first_chunk > 0 else 1
    if cap > _MAX_CHUNK:
        cap = _MAX_CHUNK
    total: int32 = 0
    while True:
        want = cap
        if max_length > 0 and max_length - total < want:
            want = max_length - total
        if want == 0:
            break
        buf = UninitHeapStorage[uint8](uint32.trunc(want))
        consumed: uint64 = 0
        produced: uint64 = 0
        out.rc = _z.step(s, unsafe_ptr_add(base, out.pos),
                         uint64(n - out.pos), buf.ptr(), uint64(want), flush,
                         take_ptr(consumed), take_ptr(produced))
        out.pos += int32.trunc(consumed)
        got = int32.trunc(produced)
        if got > 0:
            out.parts.append(unsafe_bytes_from_buf(buf.ptr(), produced))
            total += got
        if out.rc != _z.OK and out.rc != _z.BUF_ERROR:
            break
        if got < want:
            # The shim hands zlib at most 1 GiB of input per call, so
            # leftover input after a clean step is another slice.
            if out.rc != _z.OK or out.pos >= n:
                break
        else:
            cap = _next_chunk(cap)
    return out


def crc32(data: bytes, value: uint32 = 0) -> uint32:
    """CRC-32 of `data`, continuing from a previous checksum `value`."""
    return _z.crc32(value, unsafe_ptr(data), uint64(len(data)))


def adler32(data: bytes, value: uint32 = 1) -> uint32:
    """Adler-32 of `data`, continuing from a previous checksum `value`."""
    return _z.adler32(value, unsafe_ptr(data), uint64(len(data)))


def compress(data: bytes, level: int32 = -1, wbits: int32 = 15) -> bytes:
    """Compress `data` in one call. `wbits` picks the container: 9..15 zlib,
    -9..-15 raw deflate, 25..31 gzip."""
    rc: int32 = 0
    p = _z.deflate_new(level, DEFLATED, wbits, DEF_MEM_LEVEL,
                       Z_DEFAULT_STRATEGY, take_ptr(rc))
    if p is None:
        if rc == _z.MEM_ERROR:
            raise MemoryError("Out of memory while compressing data")
        if rc == _z.STREAM_ERROR:
            raise error("Bad compression level")
        raise _error(rc, None, "while compressing data")
    s = _Stream(p)
    run = _pump(s.p, data, Z_FINISH, DEF_BUF_SIZE, 0)
    if run.rc != _z.STREAM_END:
        raise _error(run.rc, _z.msg(s.p), "while compressing data")
    return run.output()


def decompress(data: bytes, wbits: int32 = 15,
               bufsize: int32 = 16384) -> bytes:
    """Decompress a complete stream in one call; trailing bytes after the
    end of the stream are ignored. `bufsize` only sizes the first output
    buffer."""
    if bufsize < 0:
        raise ValueError("bufsize must be non-negative")
    s = _Stream(_inflate_stream(wbits, False))
    run = _pump(s.p, data, Z_FINISH, bufsize, 0)
    if run.rc == _z.MEM_ERROR:
        raise MemoryError("Out of memory while decompressing data")
    if run.rc != _z.STREAM_END:
        raise _error(run.rc, _z.msg(s.p), "while decompressing data")
    return run.output()


@nocopy
class _Compress:
    """Incremental compressor; build with `compressobj()`."""
    _s: _Stream
    _ended: bool

    def __init__(self, level: int32 = -1, method: int32 = 8,
                 wbits: int32 = 15, memLevel: int32 = 8,
                 strategy: int32 = 0) -> None:
        self._s = _Stream(_deflate_stream(level, method, wbits, memLevel,
                                          strategy))
        self._ended = False

    def compress(self, data: bytes) -> bytes:
        """Compress `data`; the result may hold only part of it until a
        flush."""
        if self._ended:
            raise _error(_z.STREAM_ERROR, _z.msg(self._s.p),
                         "while compressing data")
        run = _pump(self._s.p, data, Z_NO_FLUSH, DEF_BUF_SIZE, 0)
        if run.rc == _z.STREAM_ERROR:
            raise _error(run.rc, _z.msg(self._s.p), "while compressing data")
        return run.output()

    def flush(self, mode: int32 = 4) -> bytes:
        """Emit pending output. Z_FINISH ends the stream: no further
        compress() or flush() is allowed after it."""
        if mode == Z_NO_FLUSH:
            return b""
        if self._ended:
            raise _error(_z.STREAM_ERROR, _z.msg(self._s.p), "while flushing")
        run = _pump(self._s.p, b"", mode, DEF_BUF_SIZE, 0)
        if mode == Z_FINISH and run.rc == _z.STREAM_END:
            self._ended = True
        elif run.rc != _z.OK and run.rc != _z.BUF_ERROR:
            raise _error(run.rc, _z.msg(self._s.p), "while flushing")
        return run.output()


@nocopy
class _Decompress:
    """Incremental decompressor; build with `decompressobj()`."""
    _s: _Stream
    _eof: bool
    _ended: bool
    _unused: bytes
    _tail: bytes

    def __init__(self, wbits: int32 = 15) -> None:
        self._s = _Stream(_inflate_stream(wbits, True))
        self._eof = False
        self._ended = False
        self._unused = b""
        self._tail = b""

    @property
    def eof(self) -> bool:
        """True once the end of the compressed stream has been reached."""
        return self._eof

    @property
    def unused_data(self) -> bytes:
        """Bytes seen after the end of the compressed stream."""
        return self._unused

    @property
    def unconsumed_tail(self) -> bytes:
        """Input a `max_length`-limited decompress() did not get to; pass it
        back in to continue."""
        return self._tail

    def _save_input(self, data: bytes, run: _Pump) -> None:
        # CPython's save_unconsumed_input(): past the end of the stream the
        # rest of the input is unused data; otherwise it is the tail. A tail
        # left from an earlier call is replaced by the rest of the input even
        # at the stream end, so it then repeats the unused bytes (CPython's
        # observable behavior).
        leftover = len(data) - run.pos
        if run.rc == _z.STREAM_END and leftover > 0:
            self._unused = self._unused + bytes(data[run.pos:])
            if len(self._tail) > 0:
                self._tail = bytes(data[run.pos:])
        elif leftover > 0 or len(self._tail) > 0:
            self._tail = bytes(data[run.pos:])

    def decompress(self, data: bytes, max_length: int32 = 0) -> bytes:
        """Decompress as much of `data` as possible, at most `max_length`
        output bytes when it is > 0."""
        if max_length < 0:
            raise ValueError("max_length must be non-negative")
        if self._ended:
            # The stream was finalized by flush(), as CPython's is.
            self._tail = bytes(data)
            raise _error(_z.STREAM_ERROR, _z.msg(self._s.p),
                         "while decompressing data")
        run = _pump(self._s.p, data, Z_SYNC_FLUSH, DEF_BUF_SIZE, max_length)
        self._save_input(data, run)
        if run.rc == _z.STREAM_END:
            self._eof = True
        elif run.rc != _z.OK and run.rc != _z.BUF_ERROR:
            raise _error(run.rc, _z.msg(self._s.p), "while decompressing data")
        return run.output()

    def flush(self, length: int32 = 16384) -> bytes:
        """Decompress the pending `unconsumed_tail`. Never raises for a
        truncated or corrupt stream (CPython's flush does not either)."""
        if length <= 0:
            raise ValueError("length must be greater than zero")
        if self._ended:
            return b""
        tail = self._tail
        run = _pump(self._s.p, tail, Z_FINISH, length, 0)
        self._save_input(tail, run)
        if run.rc == _z.STREAM_END:
            self._eof = True
            self._ended = True
        return run.output()


def compressobj(level: int32 = -1, method: int32 = 8, wbits: int32 = 15,
                memLevel: int32 = 8, strategy: int32 = 0) -> Own[_Compress]:
    """An incremental compressor for data too large to hold at once."""
    return _Compress(level, method, wbits, memLevel, strategy)


def decompressobj(wbits: int32 = 15) -> Own[_Decompress]:
    """An incremental decompressor for streams too large to hold at once."""
    return _Decompress(wbits)
