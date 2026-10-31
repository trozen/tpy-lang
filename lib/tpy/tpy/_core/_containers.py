__tpy__ = {"mod": "native",
           "cpp_namespace": "tpystd::tpy"}
from .._typing import Self, Iterator, Iterable
from tpy import Span, Ptr
from .._bootstrap._decorators import readonly, auto_readonly, pure, nocopy, Own, dispatch
from .._bootstrap._extern import native, cpp_template, builtin_type
from ._types import int32, Comparable, Equatable, Deref, Spannable, NativeIterable


@builtin_type("tpy.Span")
@native("std::span", borrowing_view=True)
class Span[T](Iterable[T], NativeIterable[T], Spannable[T]):
    @dispatch
    @cpp_template("{cpp}({0}, static_cast<size_t>({1}))")
    @pure
    def __init__(self, ptr: Ptr[T], length: int32, /) -> None: ...

    @dispatch
    @cpp_template("{cpp}({0})")
    @pure
    def __init__(self, source: Span[T], /) -> None: ...

    @native("tpy::__iter__", function=True)
    @pure
    @readonly
    def __iter__(self) -> Iterator[T]: ...

    @native("tpy::__len__", function=True)
    @pure
    @readonly
    def __len__(self) -> int32: ...

    @cpp_template("{self}[static_cast<std::size_t>({0})]")
    @pure
    @auto_readonly
    def unchecked_get(self, index: int32) -> auto_readonly[T]: ...

    @cpp_template("{self}[static_cast<std::size_t>({0})] = {1}")
    def unchecked_set(self, index: int32, value: Own[T]) -> None: ...

    @dispatch
    @native("tpy::__getitem__", function=True, element_access=unchecked_get)
    @pure
    @auto_readonly
    def __getitem__(self, index: int32) -> auto_readonly[T]: ...

    @dispatch
    @cpp_template("::tpy::list_slice({self}, {0})")
    @pure
    @auto_readonly
    def __getitem__(self, index: basic_slice) -> Span[auto_readonly[T]]: ...

    @dispatch
    @cpp_template("::tpy::list_stepped_slice({self}, {0})")
    @pure
    @readonly
    def __getitem__(self, index: slice) -> Own[list[T]]: ...

    @native("tpy::__setitem__", function=True, element_access=unchecked_set)
    def __setitem__(self, index: int32, value: Own[T]) -> None: ...

    @native("tpy::seq_contains", function=True, element_effect="lookup")
    @pure
    @readonly
    def __contains__[U, T: Equatable](self, value: U, /) -> bool: ...

    @native("tpy::as_span", function=True)
    @pure
    @readonly
    def __span__(self) -> Span[readonly[T]]: ...

    @native("tpy::sort_in_place", function=True)
    def sort[T: Comparable](self) -> None: ...


# Body view of a *args parameter. Span-like, but intentionally NOT Spannable
# (no __span__): a reference-element vararg can be stored as a pointer array
# (varargs<T, false> indirect mode), not contiguous T, so it has no general
# std::span<T> -- hence a vararg is not accepted where a Span[T] is expected.
# (A value-element vararg is contiguous and could expose one, but is rejected
# under the same uniform rule.) Internal only -- not user-spellable.
@builtin_type("tpy.varargs")
@native("::tpy::varargs", borrowing_view=True)
class varargs[T](Iterable[T], NativeIterable[T]):
    @native("tpy::__iter__", function=True)
    @pure
    @readonly
    def __iter__(self) -> Iterator[T]: ...

    @native("tpy::__len__", function=True)
    @pure
    @readonly
    def __len__(self) -> int32: ...

    @cpp_template("{self}[static_cast<std::size_t>({0})]")
    @pure
    @auto_readonly
    def unchecked_get(self, index: int32) -> auto_readonly[T]: ...

    @dispatch
    @native("tpy::__getitem__", function=True, element_access=unchecked_get)
    @pure
    @auto_readonly
    def __getitem__(self, index: int32) -> auto_readonly[T]: ...

    @dispatch
    @cpp_template("::tpy::list_slice({self}, {0})")
    @pure
    @auto_readonly
    def __getitem__(self, index: basic_slice) -> varargs[auto_readonly[T]]: ...

    @dispatch
    @cpp_template("::tpy::list_stepped_slice({self}, {0})")
    @pure
    @readonly
    def __getitem__(self, index: slice) -> Own[list[T]]: ...


@builtin_type("tpy.Array")
@native("std::array", elements=True)
class Array[T, N: int](Iterable[T], NativeIterable[T], Spannable[T]):
    @native("tpy::__iter__", function=True)
    @pure
    @readonly
    def __iter__(self) -> Iterator[T]: ...

    @native("tpy::__len__", function=True)
    @pure
    @readonly
    def __len__(self) -> int32: ...

    @cpp_template("{self}[static_cast<std::size_t>({0})]")
    @pure
    @auto_readonly
    def unchecked_get(self, index: int32) -> auto_readonly[T]: ...

    @cpp_template("{self}[static_cast<std::size_t>({0})] = {1}")
    def unchecked_set(self, index: int32, value: Own[T]) -> None: ...

    @dispatch
    @native("tpy::__getitem__", function=True, element_access=unchecked_get)
    @pure
    @auto_readonly
    def __getitem__(self, index: int32) -> auto_readonly[T]: ...

    @dispatch
    @cpp_template("::tpy::list_slice({self}, {0})")
    @pure
    @auto_readonly
    def __getitem__(self, index: basic_slice) -> Span[auto_readonly[T]]: ...

    @dispatch
    @cpp_template("::tpy::list_stepped_slice({self}, {0})")
    @pure
    @readonly
    def __getitem__(self, index: slice) -> Own[list[T]]: ...

    @native("tpy::__setitem__", function=True, element_access=unchecked_set)
    def __setitem__(self, index: int32, value: Own[T]) -> None: ...

    @native("tpy::seq_contains", function=True, element_effect="lookup")
    @pure
    @readonly
    def __contains__[U, T: Equatable](self, value: U, /) -> bool: ...

    @native("tpy::as_span", function=True)
    @pure
    @readonly
    def __span__(self) -> Span[readonly[T]]: ...

    @native("tpy::__repr__", function=True)
    @pure
    @readonly
    def __repr__(self) -> str: ...

    @cpp_template("{self} == {0}")
    @pure
    @readonly
    def __eq__[T: Equatable](self, other: readonly[Array[T, N]]) -> bool: ...

    @native("tpy::container_ne", function=True)
    @pure
    @readonly
    def __ne__[T: Equatable](self, other: readonly[Array[T, N]]) -> bool: ...


@builtin_type("tpy.Ptr")
@native("{T}*")
class Ptr[T](Deref[T]):
    @cpp_template("nullptr")
    @pure
    def __init__(self) -> None: ...

    @cpp_template("::tpy::deref_check({self})")
    def __deref__(self) -> T: ...

    @cpp_template("std::span({self}, static_cast<size_t>({0}))")
    @readonly
    @pure
    def span(self, length: int32) -> Span[T]: ...


@builtin_type("tpy.SpanIter")
@native("::tpy::SpanIter", borrowing_view=True)
@nocopy
class SpanIter[T](NativeIterable[T], Iterable[T], Iterator[T]):
    @cpp_template("{cpp}({0})")
    @pure
    def __init__(self, source: Span[T], /) -> None: ...

    @cpp_template("{self}.__next__()")
    def __next__(self) -> T: ...

    @cpp_template("{self}.__iter__()")
    def __iter__(self) -> Self: ...
