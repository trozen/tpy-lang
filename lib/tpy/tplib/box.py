# Box[T] -- heap-allocated owning container.
from __future__ import annotations
from typing import Self
from tpy import Own, Ptr, Deref, Covariant, readonly
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

    @readonly
    def get(self) -> T:
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

    # TODO: __eq__ -- delegate == to inner value
