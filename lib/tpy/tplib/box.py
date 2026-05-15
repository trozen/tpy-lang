# Box[T] -- heap-allocated owning container.
from __future__ import annotations
from typing import Self
from tpy import Own, Ptr, UInt64, Deref, Covariant, Equatable, Comparable, Hashable, readonly, auto_readonly
from tpy.unsafe import unsafe_take, unsafe_release, unsafe_replace, unsafe_transfer_ownership

class Box[T](Deref[T], Covariant[T]):
    _ptr: Ptr[T]

    def __init__(self, value: Own[T]):
        self._ptr = unsafe_take(value)

    def __del__(self):
        unsafe_release(self._ptr)

    # __deref__ implicitly gets dual mutable/const overloads via
    # IMPLICIT_AUTO_READONLY_METHODS in typesys; no explicit @auto_readonly
    # needed.
    def __deref__(self) -> T:
        return self.get()

    @auto_readonly
    def get(self) -> auto_readonly[T]:
        return self._ptr

    def set(self, value: Own[T]) -> None:
        self._ptr = unsafe_replace(self._ptr, value)

    @readonly
    def clone(self) -> Own[Box[T]]:
        return Box(self.get())

    def take(self: Own[Self]) -> Own[T]:
        return unsafe_transfer_ownership(self._ptr)

    def __str__(self) -> str:
        return f"Box({self.get()})"

    def __repr__(self) -> str:
        return f"Box({self.get()!r})"

    def __eq__[T: Equatable](self, other: Box[T]) -> bool:
        return self.get() == other.get()

    def __lt__[T: Comparable](self, other: Box[T]) -> bool:
        return self.get() < other.get()

    def __le__[T: Comparable](self, other: Box[T]) -> bool:
        return not other.get() < self.get()

    def __gt__[T: Comparable](self, other: Box[T]) -> bool:
        return other.get() < self.get()

    def __ge__[T: Comparable](self, other: Box[T]) -> bool:
        return not self.get() < other.get()

    def __hash__[T: Hashable](self) -> UInt64:
        return hash(self.get())
