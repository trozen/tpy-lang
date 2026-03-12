# Box[T] -- heap-allocated owning container.
from __future__ import annotations
from typing import Self
from tpy import Own, Ptr, UInt64, Deref, Covariant, Equatable, Comparable, Hashable, readonly, readonly_alt
from tpy.unsafe import unsafe_alloc, unsafe_free, unsafe_init, unsafe_drop, unsafe_move_out

class Box[T](Deref[T], Covariant[T]):
    _ptr: Ptr[T]

    def __init__(self, value: Own[T]):
        self._ptr = unsafe_alloc()
        unsafe_init(self._ptr, value)

    def __del__(self):
        unsafe_drop(self._ptr)
        unsafe_free(self._ptr)

    def __deref__(self) -> T:
        return self.get()

    @readonly_alt
    def get(self) -> readonly_alt[T]:
        return self._ptr

    def set(self, value: Own[T]) -> None:
        unsafe_drop(self._ptr)
        unsafe_init(self._ptr, value)

    @readonly
    def clone(self) -> Own[Box[T]]:
        return Box(self.get())

    def take(self: Own[Self]) -> Own[T]:
        val = unsafe_move_out(self._ptr)
        unsafe_free(self._ptr)
        return val

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
