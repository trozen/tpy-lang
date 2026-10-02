# tpy: native_module
# tpy: cpp_namespace("tpystd::builtins")
from .._typing import Self, Iterator, Iterable
from .._bootstrap._decorators import readonly, pure, Own, dispatch
from .._core._types import int32, Equatable, Comparable, NativeIterable, NativeRangeConstructible, Spannable
from .._core._containers import Span
from .._bootstrap._extern import native, cpp_template, builtin_type


@builtin_type("builtins.list")
@native("std::vector", indirecting=True, elements=True)
class list[T](Iterable[T], NativeIterable[T], NativeRangeConstructible[T], Spannable[T]):
    @pure
    @readonly
    @cpp_template("::tpy::construct<std::vector<{T}>>({0})")
    def __init__(self, x: Iterable[Own[T]]) -> None: ...

    @dispatch
    @native("tpy::__iter__", function=True)
    @pure
    @readonly
    def __iter__(self) -> Iterator[T]: ...

    @dispatch
    @native("tpy::own_iter", function=True)
    def __iter__(self: Own[Self]) -> Iterator[Own[T]]: ...

    @native("tpy::__len__", function=True)
    @pure
    @readonly
    def __len__(self) -> int32: ...

    @native("push_back")
    def append(self, value: Own[T]) -> None: ...

    @dispatch
    @native("tpy::pop_back", function=True)
    def pop(self) -> Own[T]: ...

    @dispatch
    @native("tpy::list_pop_at", function=True)
    def pop(self, index: int32) -> Own[T]: ...

    @native
    def clear(self) -> None: ...

    @cpp_template("{self}[{0}]")
    @pure
    @readonly
    def unchecked_get(self, index: int32) -> T: ...

    @dispatch
    @native("tpy::__getitem__", function=True)
    @pure
    @readonly
    def __getitem__(self, index: int32) -> T: ...

    @dispatch
    @cpp_template("::tpy::list_slice({self}, {0})")
    @pure
    @readonly
    def __getitem__(self, index: basic_slice) -> Span[T]: ...

    @dispatch
    @cpp_template("::tpy::list_stepped_slice({self}, {0})")
    @pure
    @readonly
    def __getitem__(self, index: slice) -> Own[list[T]]: ...

    @dispatch
    @native("tpy::__setitem__", function=True, mutates="elements")
    def __setitem__(self, index: int32, value: Own[T]) -> None: ...

    @dispatch
    @native("tpy::list_set_slice", function=True)
    def __setitem__(self, index: basic_slice, value: Iterable[Own[T]]) -> None: ...

    @dispatch
    @native("tpy::list_set_stepped_slice", function=True)
    def __setitem__(self, index: slice, value: Iterable[Own[T]]) -> None: ...

    @native("tpy::__delitem__", function=True)
    def __delitem__(self, index: int32) -> None: ...

    @native("tpy::list_insert", function=True)
    def insert(self, index: int32, value: Own[T]) -> None: ...

    @native("tpy::list_remove", function=True)
    def remove[T: Equatable](self, value: T) -> None: ...

    @native("tpy::list_extend", function=True)
    def extend(self, other: Iterable[Own[T]]) -> None: ...

    @native("tpy::list_index", function=True)
    @pure
    @readonly
    def index[T: Equatable](self, value: T) -> int32: ...

    @native("tpy::list_count", function=True)
    @pure
    @readonly
    def count[T: Equatable](self, value: T) -> int32: ...

    @native("tpy::list_reverse", function=True)
    def reverse(self) -> None: ...

    @native("tpy::sort_in_place", function=True)
    def sort[T: Comparable](self) -> None: ...

    @native("tpy::list_copy", function=True)
    @pure
    @readonly
    def copy(self) -> Own[list[T]]: ...

    @native("tpy::list_concat", function=True)
    @pure
    @readonly
    def __add__(self, other: list[T]) -> Own[list[T]]: ...

    @native("tpy::list_extend", function=True)
    def __iadd__(self, other: Iterable[Own[T]]) -> list[T]: ...

    @native("tpy::as_span", function=True)
    @pure
    @readonly
    def __span__(self) -> Span[readonly[T]]: ...
