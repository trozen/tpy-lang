# tpy: cpp_namespace("tpystd::tpy")
from .._typing import overload, Self, Iterator, Iterable
from tpy import Span, Ptr
from .._bootstrap._decorators import readonly, pure, nocopy, Own
from .._bootstrap._extern import native, cpp_template, builtin_type
from ._types import Int32, Comparable, Deref, ReadOnlySpanLike, NativeIterable


@builtin_type("tpy.Span")
@native("std::span")
class Span[T](Iterable[T], NativeIterable[T], ReadOnlySpanLike[T]):
    @overload
    @cpp_template("{cpp}({0}, static_cast<size_t>({1}))")
    def __init__(self, ptr: Ptr[T], length: Int32) -> None: ...

    @overload
    @cpp_template("{cpp}({0})")
    def __init__(self, source: Span[T]) -> None: ...

    @native("tpy::__iter__", function=True)
    @pure
    @readonly
    def __iter__(self) -> Iterator[T]: ...

    @native("tpy::__len__", function=True)
    @pure
    @readonly
    def __len__(self) -> Int32: ...

    @cpp_template("{self}[{0}]")
    @pure
    @readonly
    def unchecked_get(self, index: Int32) -> T: ...

    @overload
    @native("tpy::__getitem__", function=True)
    @pure
    @readonly
    def __getitem__(self, index: Int32) -> T: ...

    @overload
    @cpp_template("::tpy::list_slice({self}, {0})")
    @pure
    @readonly
    def __getitem__(self, index: basic_slice) -> Span[T]: ...

    @overload
    @cpp_template("::tpy::list_stepped_slice({self}, {0})")
    @pure
    @readonly
    def __getitem__(self, index: slice) -> Own[list[T]]: ...

    @native("tpy::__setitem__", function=True)
    def __setitem__(self, index: Int32, value: Own[T]) -> None: ...

    @native("tpy::as_span", function=True)
    @pure
    @readonly
    def __span__(self) -> Span[readonly[T]]: ...

    @cpp_template("std::stable_sort({self}.begin(), {self}.end())")
    def sort[T: Comparable](self) -> None: ...


@builtin_type("tpy.Array")
@native("std::array")
class Array[T, N: int](Iterable[T], NativeIterable[T], ReadOnlySpanLike[T]):
    @native("tpy::__iter__", function=True)
    @pure
    @readonly
    def __iter__(self) -> Iterator[T]: ...

    @native("tpy::__len__", function=True)
    @pure
    @readonly
    def __len__(self) -> Int32: ...

    @cpp_template("{self}[{0}]")
    @pure
    @readonly
    def unchecked_get(self, index: Int32) -> T: ...

    @overload
    @native("tpy::__getitem__", function=True)
    @pure
    @readonly
    def __getitem__(self, index: Int32) -> T: ...

    @overload
    @cpp_template("::tpy::list_slice({self}, {0})")
    @pure
    @readonly
    def __getitem__(self, index: basic_slice) -> Span[T]: ...

    @overload
    @cpp_template("::tpy::list_stepped_slice({self}, {0})")
    @pure
    @readonly
    def __getitem__(self, index: slice) -> Own[list[T]]: ...

    @native("tpy::__setitem__", function=True)
    def __setitem__(self, index: Int32, value: Own[T]) -> None: ...

    @native("tpy::as_span", function=True)
    @pure
    @readonly
    def __span__(self) -> Span[readonly[T]]: ...


@builtin_type("tpy.Ptr")
@native("{T}*")
class Ptr[T](Deref[T]):
    @cpp_template("nullptr")
    def __init__(self) -> None: ...

    @cpp_template("::tpy::deref_check({self})")
    def __deref__(self) -> T: ...

    @cpp_template("std::span({self}, static_cast<size_t>({0}))")
    @readonly
    @pure
    def span(self, length: Int32) -> Span[T]: ...


@builtin_type("tpy.SpanIter")
@native("::tpy::SpanIter")
@nocopy
class SpanIter[T](NativeIterable[T], Iterable[T], Iterator[T]):
    @cpp_template("{cpp}({0})")
    def __init__(self, source: Span[T]) -> None: ...

    @cpp_template("{self}.__next__()")
    def __next__(self) -> T: ...

    @cpp_template("{self}.__iter__()")
    @readonly
    def __iter__(self) -> Self: ...

