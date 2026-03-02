# Box[T] -- heap-allocated owning container.
from __future__ import annotations
from tpy import Own, Ptr, Deref, Covariant
from tpy.unsafe import unsafe_alloc, unsafe_free, unsafe_init, unsafe_drop

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

    def get(self) -> T:
        return self._ptr

    def set(self, value: Own[T]) -> None:
        unsafe_drop(self._ptr)
        unsafe_init(self._ptr, value)

    def clone(self) -> Own[Box[T]]:
        return Box(self.get())

    # TODO: __repr__ -- print contained value instead of pointer address
    # TODO: __str__ -- same, for str() conversion
    # TODO: __eq__ -- delegate == to inner value
    # TODO: take() -> Own[T] -- consume box, return owned value (needs invalidation strategy)
