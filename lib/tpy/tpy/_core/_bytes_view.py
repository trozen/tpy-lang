# tpy: native_module
# tpy: cpp_namespace("tpystd::tpy")
from .._typing import Iterator, Iterable
from .._bootstrap._decorators import readonly, pure, dispatch
from .._bootstrap._decorators import Own
from ._types import UInt8, UInt64, Int32, Comparable, Equatable, NativeIterable
from .._bootstrap._extern import native, cpp_template, builtin_type


@builtin_type("tpy.BytesView")
@native("::tpy::BytesView")
class BytesView(NativeIterable[UInt8], Iterable[UInt8], Comparable, Equatable):
    @dispatch
    @cpp_template("::tpy::BytesView()")
    def __init__(self) -> None: ...

    @native("tpy::__iter__", function=True)
    @readonly
    @pure
    def __iter__(self) -> Iterator[UInt8]: ...

    @native("tpy::__len__", function=True)
    @readonly
    @pure
    def __len__(self) -> Int32: ...

    @dispatch
    @native("tpy::bytes_getitem", function=True)
    @readonly
    @pure
    def __getitem__(self, index: Int32) -> UInt8: ...

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

    @native("tpy::bytes_contains", function=True)
    @readonly
    @pure
    def __contains__(self, value: UInt8) -> bool: ...

    @cpp_template("{self} == {0}")
    @readonly
    @pure
    def __eq__(self, other: BytesView) -> bool: ...

    @cpp_template("{self} < {0}")
    @readonly
    @pure
    def __lt__(self, other: BytesView) -> bool: ...

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
    def find(self, sub: BytesView) -> Int32: ...

    @native("tpy::bytes_rfind", function=True)
    @readonly
    @pure
    def rfind(self, sub: BytesView) -> Int32: ...

    @native("tpy::bytes_count", function=True)
    @readonly
    @pure
    def count(self, sub: BytesView) -> Int32: ...

    @native("tpy::bytes_startswith", function=True)
    @readonly
    @pure
    def startswith(self, prefix: BytesView) -> bool: ...

    @native("tpy::bytes_endswith", function=True)
    @readonly
    @pure
    def endswith(self, suffix: BytesView) -> bool: ...

    @native("tpy::bytes_replace", function=True)
    @readonly
    @pure
    def replace(self, old: BytesView, new: BytesView) -> bytes: ...

    @native("tpy::bytes_split", function=True)
    @readonly
    @pure
    def split(self, sep: BytesView) -> Own[list[bytes]]: ...

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
    def rstrip(self, chars: BytesView) -> BytesView: ...

    @native("tpy::bytes_upper", function=True)
    @readonly
    @pure
    def upper(self) -> bytes: ...
