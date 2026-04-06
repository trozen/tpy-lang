# tpy: cpp_namespace("tpystd::builtins")
from .._typing import overload, Self, Iterator, Iterable
from .._bootstrap._decorators import readonly, pure, Own
from .._core._types import UInt8, UInt64, Int32, Equatable, NativeIterable
from .._bootstrap._extern import native, cpp_template, builtin_type
from tpy import BytesView


@builtin_type("builtins.bytes")
@native("std::vector<uint8_t>")
class bytes(NativeIterable[UInt8], Iterable[UInt8], Equatable):
    @overload
    @cpp_template("std::vector<uint8_t>()")
    def __init__(self) -> None: ...
    @overload
    @native("tpy::bytes_copy", function=True)
    def __init__(self, x: bytes) -> None: ...
    @overload
    @native("tpy::bytes_copy", function=True)
    def __init__(self, x: BytesView) -> None: ...
    @overload
    @native("tpy::bytes_from_size", function=True)
    def __init__(self, x: Int32) -> None: ...

    @native("tpy::__iter__", function=True)
    @readonly
    @pure
    def __iter__(self) -> Iterator[UInt8]: ...

    @native("tpy::__len__", function=True)
    @readonly
    @pure
    def __len__(self) -> Int32: ...

    @native("tpy::bytes_getitem", function=True)
    @readonly
    @pure
    def __getitem__(self, index: Int32) -> UInt8: ...

    @overload
    @native("tpy::bytes_contains", function=True)
    @readonly
    @pure
    def __contains__(self, value: UInt8) -> bool: ...
    @overload
    @native("tpy::bytes_contains_sub", function=True)
    @readonly
    @pure
    def __contains__(self, value: bytes) -> bool: ...

    @overload
    @native("tpy::bytes_concat", function=True)
    @readonly
    def __add__(self, other: bytes) -> bytes: ...
    @overload
    @native("tpy::bytes_concat", function=True)
    @readonly
    def __add__(self, other: bytearray) -> bytes: ...
    @overload
    @native("tpy::bytes_concat", function=True)
    @readonly
    def __add__(self, other: BytesView) -> bytes: ...

    @native("tpy::bytes_repeat", function=True)
    @readonly
    @pure
    def __mul__(self, n: Int32) -> bytes: ...

    @native("tpy::bytes_repeat", function=True)
    @readonly
    @pure
    def __rmul__(self, n: Int32) -> bytes: ...

    @native("tpy::bytes_eq", function=True)
    @readonly
    @pure
    def __eq__(self, other: bytes) -> bool: ...

    @cpp_template("::tpy::__hash__({self})")
    @readonly
    @pure
    def __hash__(self) -> UInt64: ...

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
    def find(self, sub: bytes) -> Int32: ...

    @native("tpy::bytes_rfind", function=True)
    @readonly
    @pure
    def rfind(self, sub: bytes) -> Int32: ...

    @native("tpy::bytes_count", function=True)
    @readonly
    @pure
    def count(self, sub: bytes) -> Int32: ...

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

    @native("tpy::bytes_strip", function=True)
    @readonly
    @pure
    def strip(self) -> bytes: ...

    @native("tpy::bytes_lstrip", function=True)
    @readonly
    @pure
    def lstrip(self) -> bytes: ...

    @overload
    @native("tpy::bytes_rstrip", function=True)
    @readonly
    @pure
    def rstrip(self) -> bytes: ...
    @overload
    @native("tpy::bytes_rstrip_chars", function=True)
    @readonly
    @pure
    def rstrip(self, chars: bytes) -> bytes: ...

    @native("tpy::bytes_upper", function=True)
    @readonly
    @pure
    def upper(self) -> bytes: ...


@builtin_type("builtins.bytearray")
@native("std::vector<uint8_t>")
class bytearray(NativeIterable[UInt8], Iterable[UInt8], Equatable):
    @overload
    @cpp_template("std::vector<uint8_t>()")
    def __init__(self) -> None: ...
    @overload
    @native("tpy::bytes_copy", function=True)
    def __init__(self, x: bytes) -> None: ...
    @overload
    @native("tpy::bytes_copy", function=True)
    def __init__(self, x: BytesView) -> None: ...
    @overload
    @native("tpy::bytes_copy", function=True)
    def __init__(self, x: bytearray) -> None: ...
    @overload
    @native("tpy::bytes_from_size", function=True)
    def __init__(self, x: Int32) -> None: ...

    @native("tpy::__iter__", function=True)
    @readonly
    @pure
    def __iter__(self) -> Iterator[UInt8]: ...

    @native("tpy::__len__", function=True)
    @readonly
    @pure
    def __len__(self) -> Int32: ...

    @native("tpy::bytes_getitem", function=True)
    @readonly
    @pure
    def __getitem__(self, index: Int32) -> UInt8: ...

    @overload
    @native("tpy::bytes_contains", function=True)
    @readonly
    @pure
    def __contains__(self, value: UInt8) -> bool: ...
    @overload
    @native("tpy::bytes_contains_sub", function=True)
    @readonly
    @pure
    def __contains__(self, value: bytes) -> bool: ...

    @native("tpy::bytearray_setitem", function=True)
    def __setitem__(self, index: Int32, value: UInt8) -> None: ...

    @overload
    @native("tpy::bytes_concat", function=True)
    @readonly
    def __add__(self, other: bytes) -> bytearray: ...
    @overload
    @native("tpy::bytes_concat", function=True)
    @readonly
    def __add__(self, other: bytearray) -> bytearray: ...
    @overload
    @native("tpy::bytes_concat", function=True)
    @readonly
    def __add__(self, other: BytesView) -> bytearray: ...

    @native("tpy::bytes_repeat", function=True)
    @readonly
    @pure
    def __mul__(self, n: Int32) -> bytearray: ...

    @native("tpy::bytes_eq", function=True)
    @readonly
    @pure
    def __eq__(self, other: bytearray) -> bool: ...

    @native("push_back")
    def append(self, value: UInt8) -> None: ...

    @cpp_template("({self}).insert(({self}).end(), ({0}).begin(), ({0}).end())")
    def extend(self, other: Iterable[UInt8]) -> None: ...

    @overload
    @native("tpy::bytearray_pop", function=True)
    def pop(self) -> UInt8: ...
    @overload
    @native("tpy::bytearray_pop_at", function=True)
    def pop(self, index: Int32) -> UInt8: ...

    @native
    def clear(self) -> None: ...

    @native("tpy::bytearray_insert", function=True)
    def insert(self, index: Int32, value: UInt8) -> None: ...

    @native("tpy::bytearray_remove", function=True)
    def remove(self, value: UInt8) -> None: ...

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
    def find(self, sub: bytes) -> Int32: ...

    @native("tpy::bytes_rfind", function=True)
    @readonly
    @pure
    def rfind(self, sub: bytes) -> Int32: ...

    @native("tpy::bytes_count", function=True)
    @readonly
    @pure
    def count(self, sub: bytes) -> Int32: ...

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

    @native("tpy::bytes_strip", function=True)
    @readonly
    @pure
    def strip(self) -> bytearray: ...

    @native("tpy::bytes_lstrip", function=True)
    @readonly
    @pure
    def lstrip(self) -> bytearray: ...

    @overload
    @native("tpy::bytes_rstrip", function=True)
    @readonly
    @pure
    def rstrip(self) -> bytearray: ...
    @overload
    @native("tpy::bytes_rstrip_chars", function=True)
    @readonly
    @pure
    def rstrip(self, chars: bytes) -> bytearray: ...

    @native("tpy::bytes_upper", function=True)
    @readonly
    @pure
    def upper(self) -> bytearray: ...
