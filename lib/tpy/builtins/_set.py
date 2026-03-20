from typing import overload, Iterator, Iterable
from tpy import Int32, Own, readonly, pure
from tpy import NativeIterable
from tpy.extern import native, cpp_template, builtin_type


@builtin_type("builtins.set")
@native("tpy::ordered_set")
class set[T](Iterable[T], NativeIterable[T]):
    @overload
    @pure
    @readonly
    @cpp_template("::tpy::set_collect<{T}>({0})")
    def __init__(self, x: Iterator[T]) -> None: ...

    @overload
    @pure
    @readonly
    @cpp_template("::tpy::set_from_range<{T}>({0})")
    def __init__(self, x: Iterable[Own[T]]) -> None: ...

    @native("tpy::__iter__", function=True)
    @pure
    @readonly
    def __iter__(self) -> Iterator[T]: ...

    @native("tpy::__len__", function=True)
    @pure
    @readonly
    def __len__(self) -> Int32: ...

    @native("contains")
    @pure
    @readonly
    def __contains__(self, value: T) -> bool: ...

    @native("insert")
    def add(self, value: Own[T]) -> None: ...

    @native("erase")
    def discard(self, value: T) -> None: ...

    @native("tpy::set_remove", function=True)
    def remove(self, value: T) -> None: ...

    @native("tpy::set_pop", function=True)
    def pop(self) -> Own[T]: ...

    @native
    def clear(self) -> None: ...

    @native("tpy::set_copy", function=True)
    @pure
    @readonly
    def copy(self) -> Own[set[T]]: ...

    # Set algebra (return new set)
    @native("tpy::set_union", function=True)
    @pure
    @readonly
    def union(self, other: set[T]) -> Own[set[T]]: ...

    @native("tpy::set_intersection", function=True)
    @pure
    @readonly
    def intersection(self, other: set[T]) -> Own[set[T]]: ...

    @native("tpy::set_difference", function=True)
    @pure
    @readonly
    def difference(self, other: set[T]) -> Own[set[T]]: ...

    @native("tpy::set_symmetric_difference", function=True)
    @pure
    @readonly
    def symmetric_difference(self, other: set[T]) -> Own[set[T]]: ...

    # Set predicates
    @native("tpy::set_issubset", function=True)
    @pure
    @readonly
    def issubset(self, other: set[T]) -> bool: ...

    @native("tpy::set_issuperset", function=True)
    @pure
    @readonly
    def issuperset(self, other: set[T]) -> bool: ...

    @native("tpy::set_isdisjoint", function=True)
    @pure
    @readonly
    def isdisjoint(self, other: set[T]) -> bool: ...

    # In-place set algebra
    @native("tpy::set_update", function=True)
    def update(self, other: set[T]) -> None: ...

    @native("tpy::set_intersection_update", function=True)
    def intersection_update(self, other: set[T]) -> None: ...

    @native("tpy::set_difference_update", function=True)
    def difference_update(self, other: set[T]) -> None: ...

    @native("tpy::set_symmetric_difference_update", function=True)
    def symmetric_difference_update(self, other: set[T]) -> None: ...

    # Operators (same as named methods)
    @native("tpy::set_union", function=True)
    @pure
    @readonly
    def __or__(self, other: set[T]) -> Own[set[T]]: ...

    @native("tpy::set_intersection", function=True)
    @pure
    @readonly
    def __and__(self, other: set[T]) -> Own[set[T]]: ...

    @native("tpy::set_difference", function=True)
    @pure
    @readonly
    def __sub__(self, other: set[T]) -> Own[set[T]]: ...

    @native("tpy::set_symmetric_difference", function=True)
    @pure
    @readonly
    def __xor__(self, other: set[T]) -> Own[set[T]]: ...

    # In-place operators
    @native("tpy::set_update", function=True)
    def __ior__(self, other: set[T]) -> set[T]: ...

    @native("tpy::set_intersection_update", function=True)
    def __iand__(self, other: set[T]) -> set[T]: ...

    @native("tpy::set_difference_update", function=True)
    def __isub__(self, other: set[T]) -> set[T]: ...

    @native("tpy::set_symmetric_difference_update", function=True)
    def __ixor__(self, other: set[T]) -> set[T]: ...
