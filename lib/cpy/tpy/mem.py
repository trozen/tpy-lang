"""
TurboPython memory primitives (tpy.mem).

CPython implementations of uninitialized storage types used for building
low-level containers like Box[T].
"""

from __future__ import annotations
from typing import Generic, TypeVar

T = TypeVar('T')


class _StorageMeta(type):
    """Metaclass to support subscript syntax for storage types."""

    def __getitem__(cls, params):
        if cls is UninitArrayStorage:
            if not isinstance(params, tuple) or len(params) != 2:
                raise TypeError("UninitArrayStorage requires [T, N] syntax")
            elem_type, capacity = params

            class Bound(UninitArrayStorage):
                _elem_type = elem_type
                _capacity = capacity

            return Bound
        else:
            # UninitHeapStorage[T]
            class Bound(UninitHeapStorage):
                _elem_type = params

            return Bound


class UninitArrayStorage(metaclass=_StorageMeta):
    """Inline uninitialized storage for N elements of type T."""

    _elem_type = None
    _capacity = 0

    def __init__(self):
        self._slots: dict[int, object] = {}

    def init(self, index: int, value: object) -> None:
        self._slots[index] = value

    def drop(self, index: int) -> None:
        del self._slots[index]

    def load(self, index: int) -> object:
        return self._slots[index]

    def init0(self, value: object) -> None:
        self.init(0, value)

    def drop0(self) -> None:
        self.drop(0)

    def load0(self) -> object:
        return self.load(0)

    def take(self, index: int) -> object:
        val = self._slots.pop(index)
        return val

    def take0(self) -> object:
        return self.take(0)

    def ptr(self):
        from tpy import Ptr
        return Ptr(self)


class UninitHeapStorage(metaclass=_StorageMeta):
    """Heap-allocated uninitialized storage for elements of type T."""

    _elem_type = None

    def __init__(self, capacity: int = 1):
        self._capacity = capacity
        self._slots: dict[int, object] = {}

    def init(self, index: int, value: object) -> None:
        self._slots[index] = value

    def drop(self, index: int) -> None:
        if index in self._slots:
            del self._slots[index]

    def load(self, index: int) -> object:
        return self._slots[index]

    def init0(self, value: object) -> None:
        self.init(0, value)

    def drop0(self) -> None:
        self.drop(0)

    def load0(self) -> object:
        return self.load(0)

    def take(self, index: int) -> object:
        val = self._slots.pop(index)
        return val

    def take0(self) -> object:
        return self.take(0)

    def ptr(self):
        from tpy import Ptr
        return Ptr(self)
