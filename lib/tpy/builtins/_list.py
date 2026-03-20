from typing import overload, Iterator, Iterable
from tpy import Int32, Own, Span, readonly, pure
from tpy import Equatable, Comparable, NativeIterable, NativeRangeConstructible, ReadOnlySpanLike
from tpy.extern import native, cpp_template, native_preserves_refs, builtin_type


@builtin_type("builtins.list")
@native("std::vector")
class list[T](Iterable[T], NativeIterable[T], NativeRangeConstructible[T], ReadOnlySpanLike[T]):
    @overload
    @pure
    @readonly
    @cpp_template("::tpy::collect<std::vector<{T}>>({0})")
    def __init__(self, x: Iterator[T]) -> None: ...

    @overload
    @pure
    @readonly
    @cpp_template("::tpy::from_range<std::vector<{T}>>({0})")
    def __init__(self, x: Iterable[Own[T]]) -> None: ...

    @native("tpy::__iter__", function=True)
    @pure
    @readonly
    def __iter__(self) -> Iterator[T]: ...

    @native("tpy::__len__", function=True)
    @pure
    @readonly
    def __len__(self) -> Int32: ...

    @native("push_back")
    def append(self, value: Own[T]) -> None: ...

    @overload
    @native("tpy::pop_back", function=True)
    def pop(self) -> T: ...

    @overload
    @native("tpy::list_pop_at", function=True)
    def pop(self, index: Int32) -> T: ...

    @native
    def clear(self) -> None: ...

    @cpp_template("{self}[{0}]")
    @pure
    @readonly
    def unchecked_get(self, index: Int32) -> T: ...

    @native("tpy::__getitem__", function=True)
    @pure
    @readonly
    def __getitem__(self, index: Int32) -> T: ...

    @native("tpy::__setitem__", function=True)
    @native_preserves_refs
    def __setitem__(self, index: Int32, value: Own[T]) -> None: ...

    @native("tpy::__delitem__", function=True)
    def __delitem__(self, index: Int32) -> None: ...

    @native("tpy::list_insert", function=True)
    def insert(self, index: Int32, value: Own[T]) -> None: ...

    @native("tpy::list_remove", function=True)
    def remove[T: Equatable](self, value: T) -> None: ...

    @native("tpy::list_extend", function=True)
    def extend(self, other: Iterable[Own[T]]) -> None: ...

    @native("tpy::list_index", function=True)
    @pure
    @readonly
    def index[T: Equatable](self, value: T) -> Int32: ...

    @native("tpy::list_count", function=True)
    @pure
    @readonly
    def count[T: Equatable](self, value: T) -> Int32: ...

    @native("tpy::list_reverse", function=True)
    def reverse(self) -> None: ...

    @cpp_template("std::stable_sort({self}.begin(), {self}.end())")
    def sort[T: Comparable](self) -> None: ...

    @native("tpy::list_copy", function=True)
    @pure
    @readonly
    def copy(self) -> Own[list[T]]: ...

    @native("tpy::list_concat", function=True)
    @pure
    @readonly
    def __add__(self, other: list[T]) -> list[T]: ...

    @native("tpy::list_extend", function=True)
    def __iadd__(self, other: Iterable[Own[T]]) -> list[T]: ...

    @native("tpy::as_span", function=True)
    @pure
    @readonly
    def __span__(self) -> Span[readonly[T]]: ...
