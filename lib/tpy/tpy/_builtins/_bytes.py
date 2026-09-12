# tpy: native_module
# tpy: cpp_namespace("tpystd::builtins")
from .._typing import Self, Iterator, Iterable
from .._bootstrap._decorators import readonly, pure, Own, dispatch
from .._core._types import uint8, uint64, int32, Comparable, Equatable, NativeIterable
from .._bootstrap._extern import native, cpp_template, builtin_type
from tpy import BytesView


@builtin_type("builtins.bytes")
@native("::tpy::Bytes")
class bytes(NativeIterable[uint8], Iterable[uint8], Comparable, Equatable):
    @dispatch
    @cpp_template("::tpy::Bytes()")
    def __init__(self) -> None: ...
    # The owned type constructs from its own family's view, so the copy IS the
    # constructor -- no helper beside it. A `bytes`/`BytesView` argument arrives
    # as the span and takes the span ctor; a `bytearray` argument arrives as a
    # reference and binds the base-const-ref ctor (derived-to-base beats the
    # span's user-defined conversion), so neither needs a spelling at the site.
    @dispatch
    @cpp_template("::tpy::Bytes({0})")
    def __init__(self, x: bytes) -> None: ...
    @dispatch
    @cpp_template("::tpy::Bytes({0})")
    def __init__(self, x: bytearray) -> None: ...
    @dispatch
    @cpp_template("::tpy::Bytes({0})")
    def __init__(self, x: BytesView) -> None: ...
    @dispatch
    @native("tpy::bytes_from_size", function=True)
    def __init__(self, x: int32) -> None: ...
    @dispatch
    @cpp_template("::tpy::construct<::tpy::Bytes>({0})")
    def __init__(self, x: Iterable[uint8]) -> None: ...
    # TODO(hot-path): the int32 overload below is per-element range-checked.
    @dispatch
    @native("tpy::bytes_from_int_iterable", function=True)
    def __init__(self, x: Iterable[int32]) -> None: ...

    @native("tpy::__iter__", function=True)
    @readonly
    @pure
    def __iter__(self) -> Iterator[uint8]: ...

    @native("tpy::__len__", function=True)
    @readonly
    @pure
    def __len__(self) -> int32: ...

    @dispatch
    @native("tpy::bytes_getitem", function=True)
    @readonly
    @pure
    def __getitem__(self, index: int32) -> uint8: ...

    @dispatch
    @cpp_template("::tpy::bytes_slice({self}, {0})")
    @readonly
    @pure
    def __getitem__(self, index: basic_slice) -> BytesView: ...

    @dispatch
    @cpp_template("::tpy::bytes_stepped_slice({self}, {0})")
    @readonly
    @pure
    def __getitem__(self, index: slice) -> bytes: ...

    @dispatch
    @native("tpy::bytes_contains", function=True)
    @readonly
    @pure
    def __contains__(self, value: uint8) -> bool: ...
    @dispatch
    @native("tpy::bytes_contains_sub", function=True)
    @readonly
    @pure
    def __contains__(self, value: bytes) -> bool: ...

    @dispatch
    @native("tpy::bytes_concat", function=True)
    @readonly
    def __add__(self, other: bytes) -> bytes: ...
    @dispatch
    @native("tpy::bytes_concat", function=True)
    @readonly
    def __add__(self, other: bytearray) -> bytes: ...
    @dispatch
    @native("tpy::bytes_concat", function=True)
    @readonly
    def __add__(self, other: BytesView) -> bytes: ...

    @native("tpy::bytes_repeat", function=True)
    @readonly
    @pure
    def __mul__(self, n: int32) -> bytes: ...

    @native("tpy::bytes_repeat", function=True)
    @readonly
    @pure
    def __rmul__(self, n: int32) -> bytes: ...

    @cpp_template("{self} == {0}")
    @readonly
    @pure
    def __eq__(self, other: bytes) -> bool: ...

    @cpp_template("{self} < {0}")
    @readonly
    @pure
    def __lt__(self, other: bytes) -> bool: ...

    @cpp_template("::tpy::__hash__({self})")
    @readonly
    @pure
    def __hash__(self) -> uint64: ...

    @native("tpy::bytes_decode", function=True)
    @readonly
    @pure
    def decode(self) -> str: ...

    @native("tpy::bytes_hex", function=True)
    @readonly
    @pure
    def hex(self) -> str: ...

    @native("tpy::bytes_find", function=True)
    @readonly
    @pure
    def find(self, sub: bytes) -> int32: ...

    @native("tpy::bytes_rfind", function=True)
    @readonly
    @pure
    def rfind(self, sub: bytes) -> int32: ...

    @native("tpy::bytes_count", function=True)
    @readonly
    @pure
    def count(self, sub: bytes) -> int32: ...

    @native("tpy::bytes_startswith", function=True)
    @readonly
    @pure
    def startswith(self, prefix: bytes) -> bool: ...

    @native("tpy::bytes_endswith", function=True)
    @readonly
    @pure
    def endswith(self, suffix: bytes) -> bool: ...

    @native("tpy::bytes_replace", function=True)
    @readonly
    @pure
    def replace(self, old: bytes, new: bytes) -> bytes: ...

    @native("tpy::bytes_split", function=True)
    @readonly
    @pure
    def split(self, sep: bytes) -> Own[list[bytes]]: ...

    @native("tpy::bytes_join", function=True)
    @readonly
    @pure
    def join(self, items: Iterable[bytes]) -> bytes: ...

    # strip/lstrip/rstrip return a VIEW of the receiver (a contiguous sub-range),
    # matching str.strip -> StrView; zero-copy. Use an owned `bytes` annotation
    # on the result to keep a copy past the receiver's lifetime.
    @native("tpy::bytes_strip_view", function=True)
    @readonly
    @pure
    def strip(self) -> BytesView: ...

    @native("tpy::bytes_lstrip_view", function=True)
    @readonly
    @pure
    def lstrip(self) -> BytesView: ...

    @dispatch
    @native("tpy::bytes_rstrip_view", function=True)
    @readonly
    @pure
    def rstrip(self) -> BytesView: ...
    @dispatch
    @native("tpy::bytes_rstrip_chars_view", function=True)
    @readonly
    @pure
    def rstrip(self, chars: bytes) -> BytesView: ...

    @native("tpy::bytes_upper", function=True)
    @readonly
    @pure
    def upper(self) -> bytes: ...


@builtin_type("builtins.bytearray")
@native("::tpy::ByteArray")
class bytearray(NativeIterable[uint8], Iterable[uint8], Comparable, Equatable):
    @dispatch
    @cpp_template("::tpy::ByteArray()")
    def __init__(self) -> None: ...
    # Concrete overloads first, same rationale as in `bytes` above.
    @dispatch
    @cpp_template("::tpy::ByteArray({0})")
    def __init__(self, x: bytes) -> None: ...
    @dispatch
    @cpp_template("::tpy::ByteArray({0})")
    def __init__(self, x: BytesView) -> None: ...
    @dispatch
    @cpp_template("::tpy::ByteArray({0})")
    def __init__(self, x: bytearray) -> None: ...
    @dispatch
    @native("tpy::bytearray_from_size", function=True)
    def __init__(self, x: int32) -> None: ...
    @dispatch
    @cpp_template("::tpy::construct<::tpy::ByteArray>({0})")
    def __init__(self, x: Iterable[uint8]) -> None: ...
    # TODO(hot-path): the int32 overload below is per-element range-checked.
    @dispatch
    @native("tpy::bytearray_from_int_iterable", function=True)
    def __init__(self, x: Iterable[int32]) -> None: ...

    @native("tpy::__iter__", function=True)
    @readonly
    @pure
    def __iter__(self) -> Iterator[uint8]: ...

    @native("tpy::__len__", function=True)
    @readonly
    @pure
    def __len__(self) -> int32: ...

    @dispatch
    @native("tpy::bytes_getitem", function=True)
    @readonly
    @pure
    def __getitem__(self, index: int32) -> uint8: ...

    @dispatch
    @cpp_template("::tpy::bytes_slice({self}, {0})")
    @readonly
    @pure
    def __getitem__(self, index: basic_slice) -> BytesView: ...

    @dispatch
    @cpp_template("::tpy::bytes_stepped_slice({self}, {0})")
    @readonly
    @pure
    def __getitem__(self, index: slice) -> bytes: ...

    @dispatch
    @cpp_template("{self} == {0}")
    @readonly
    @pure
    def __eq__(self, other: bytearray) -> bool: ...

    @cpp_template("{self} < {0}")
    @readonly
    @pure
    def __lt__(self, other: bytearray) -> bool: ...

    @native("tpy::bytes_contains", function=True)
    @readonly
    @pure
    def __contains__(self, value: uint8) -> bool: ...
    @dispatch
    @native("tpy::bytes_contains_sub", function=True)
    @readonly
    @pure
    def __contains__(self, value: bytes) -> bool: ...

    @native("tpy::bytearray_setitem", function=True)
    def __setitem__(self, index: int32, value: uint8) -> None: ...

    @dispatch
    @native("tpy::bytearray_concat", function=True)
    @readonly
    def __add__(self, other: bytes) -> Own[bytearray]: ...
    @dispatch
    @native("tpy::bytearray_concat", function=True)
    @readonly
    def __add__(self, other: bytearray) -> Own[bytearray]: ...
    @dispatch
    @native("tpy::bytearray_concat", function=True)
    @readonly
    def __add__(self, other: BytesView) -> Own[bytearray]: ...

    @native("tpy::bytearray_repeat", function=True)
    @readonly
    @pure
    def __mul__(self, n: int32) -> Own[bytearray]: ...

    @native("push_back")
    def append(self, value: uint8) -> None: ...

    @dispatch
    @native("tpy::extend", function=True)
    def extend(self, other: Iterable[uint8]) -> None: ...
    # TODO(hot-path): the int32 overload below is per-element range-checked.
    @dispatch
    @native("tpy::bytes_extend_int_iterable", function=True)
    def extend(self, other: Iterable[int32]) -> None: ...

    @dispatch
    @native("tpy::bytearray_pop", function=True)
    def pop(self) -> uint8: ...
    @dispatch
    @native("tpy::bytearray_pop_at", function=True)
    def pop(self, index: int32) -> uint8: ...

    @native
    def clear(self) -> None: ...

    @native("tpy::bytearray_insert", function=True)
    def insert(self, index: int32, value: uint8) -> None: ...

    @native("tpy::bytearray_remove", function=True)
    def remove(self, value: uint8) -> None: ...

    @native("tpy::bytes_decode", function=True)
    @readonly
    @pure
    def decode(self) -> str: ...

    @native("tpy::bytes_hex", function=True)
    @readonly
    @pure
    def hex(self) -> str: ...

    @native("tpy::bytes_find", function=True)
    @readonly
    @pure
    def find(self, sub: bytes) -> int32: ...

    @native("tpy::bytes_rfind", function=True)
    @readonly
    @pure
    def rfind(self, sub: bytes) -> int32: ...

    @native("tpy::bytes_count", function=True)
    @readonly
    @pure
    def count(self, sub: bytes) -> int32: ...

    @native("tpy::bytes_startswith", function=True)
    @readonly
    @pure
    def startswith(self, prefix: bytes) -> bool: ...

    @native("tpy::bytes_endswith", function=True)
    @readonly
    @pure
    def endswith(self, suffix: bytes) -> bool: ...

    @native("tpy::bytes_replace", function=True)
    @readonly
    @pure
    def replace(self, old: bytes, new: bytes) -> bytes: ...

    @native("tpy::bytes_split", function=True)
    @readonly
    @pure
    def split(self, sep: bytes) -> Own[list[bytes]]: ...

    @native("tpy::bytes_join", function=True)
    @readonly
    @pure
    def join(self, items: Iterable[bytes]) -> bytes: ...

    # strip/lstrip/rstrip return an OWNED bytearray copy, matching CPython. A
    # view would alias the mutable buffer (mutations visible through it, unlike
    # CPython's independent copy); immutable `bytes` returns a view, mutable
    # `bytearray` copies.
    @native("tpy::bytes_strip", function=True)
    @readonly
    @pure
    def strip(self) -> bytearray: ...

    @native("tpy::bytes_lstrip", function=True)
    @readonly
    @pure
    def lstrip(self) -> bytearray: ...

    @dispatch
    @native("tpy::bytes_rstrip", function=True)
    @readonly
    @pure
    def rstrip(self) -> bytearray: ...
    @dispatch
    @native("tpy::bytes_rstrip_chars", function=True)
    @readonly
    @pure
    def rstrip(self, chars: bytes) -> bytearray: ...

    @native("tpy::bytearray_upper", function=True)
    @readonly
    @pure
    def upper(self) -> bytearray: ...
