# tpy: cpp_namespace("tpystd::_bindings::zlib")
# tpy: include("<tpy/stdlib/zlib_h.hpp>")
# tpy: link("zlib", managed=True)
"""Raw zlib bindings over the TPy z_stream shim. Not for direct user import.

The surface is the `tpy_zs_*` / `tpy_zlib_*` API implemented in
runtime/cpp/src/stdlib/zlib_shim.c (see that file + the facade header for
why a z_stream lives behind an opaque handle). The public `zlib` module
owns each `Ptr[Stream]` in a @nocopy object that frees it in __del__, and
`gzip` is pure TPy on top of `zlib`.

Buffers are (pointer, length) pairs owned by the caller on every call; the
stream keeps only zlib's internal state between calls.

zlib's code values are part of its stable ABI; the ones the facade needs
are mirrored here under private names (the public `zlib` module exposes
CPython's names).
"""

from typing import Final
from tpy import Ptr, uint8, uint32, int32, uint64, readonly
from tpy.extern import native


# Opaque stream handle: `Ptr[Stream]` codegens to `tpy_zstream*`.
@native("::tpy_zstream")
class Stream: ...


OK: Final[int32] = 0
STREAM_END: Final[int32] = 1
NEED_DICT: Final[int32] = 2
STREAM_ERROR: Final[int32] = -2
DATA_ERROR: Final[int32] = -3
MEM_ERROR: Final[int32] = -4
BUF_ERROR: Final[int32] = -5
VERSION_ERROR: Final[int32] = -6


@native("::tpy_zs_inflate_new")
def inflate_new(wbits: int32, rc: Ptr[int32]) -> Ptr[Stream]: ...

@native("::tpy_zs_deflate_new")
def deflate_new(level: int32, method: int32, wbits: int32, memlevel: int32,
                strategy: int32, rc: Ptr[int32]) -> Ptr[Stream]: ...

# One inflate()/deflate() call; `consumed` / `produced` receive the input
# bytes used and output bytes written. Returns zlib's code.
@native("::tpy_zs_step")
def step(s: Ptr[Stream], inp: Ptr[readonly[uint8]], in_len: uint64,
         out: Ptr[uint8], out_cap: uint64, flush: int32,
         consumed: Ptr[uint64], produced: Ptr[uint64]) -> int32: ...

# zlib's message for the stream's last error; None when it set none.
@native("::tpy_zs_msg")
def msg(s: Ptr[Stream]) -> Ptr[readonly[uint8]]: ...

@native("::tpy_zs_free")
def free(s: Ptr[Stream]) -> None: ...

@native("::tpy_zlib_crc32")
def crc32(value: uint32, data: Ptr[readonly[uint8]], length: uint64) -> uint32: ...

@native("::tpy_zlib_adler32")
def adler32(value: uint32, data: Ptr[readonly[uint8]], length: uint64) -> uint32: ...

@native("::tpy_zlib_runtime_version")
def runtime_version() -> Ptr[readonly[uint8]]: ...

@native("::tpy_zlib_header_version")
def header_version() -> Ptr[readonly[uint8]]: ...
