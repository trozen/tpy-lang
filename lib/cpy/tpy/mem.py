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
        import sys
        self._resolved_capacity = self._capacity if isinstance(self._capacity, int) else sys.maxsize

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

    def capacity(self) -> int:
        return self._resolved_capacity

    def init_from_span(self, start: int, src) -> None:
        for i, val in enumerate(src):
            self._slots[start + i] = val

    def drop_n(self, start: int, count: int) -> None:
        for i in range(start, start + count):
            del self._slots[i]

    def shift(self, src: int, dst: int, count: int) -> None:
        if count <= 0 or src == dst:
            return
        items = [self._slots.pop(src + i) for i in range(count)]
        for i, val in enumerate(items):
            self._slots[dst + i] = val

    def __getitem__(self, index: int) -> object:
        return self._slots[index]

    def ptr(self):
        # Capacity-1 storage (Rc.new's inline _RcCell.storage): the user
        # treats `.ptr()` as a Ptr[T] that derefs to T -- attribute access
        # must reach the inline value, not the storage object. Multi-element
        # storage (ArrayList's backing) reaches its elements via the returned
        # ptr's .span(n), where the standard _Ptr(self) already proxies
        # through `_slots[i]`.
        if self._resolved_capacity == 1:
            return _SlotPtr(self)
        from tpy import take_ptr
        return take_ptr(self)


class _SlotPtr:
    """CPython proxy for `UninitArrayStorage.ptr()` -- attribute access
    forwards to `_slots[0]`, matching TPy's `Ptr[T]` derefs-to-T semantics.
    """
    __slots__ = ('_storage',)

    def __init__(self, storage):
        object.__setattr__(self, '_storage', storage)

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, '_storage')._slots[0], name)

    def __setattr__(self, name, value):
        if name == '_storage':
            object.__setattr__(self, name, value)
        else:
            setattr(object.__getattribute__(self, '_storage')._slots[0], name, value)

    def __deref__(self):
        return object.__getattribute__(self, '_storage')._slots[0]

    def __str__(self):
        return str(object.__getattribute__(self, '_storage')._slots[0])

    def __repr__(self):
        return repr(object.__getattribute__(self, '_storage')._slots[0])

    def __format__(self, spec):
        return format(object.__getattribute__(self, '_storage')._slots[0], spec)

    def __eq__(self, other):
        v = object.__getattribute__(self, '_storage')._slots[0]
        ov = object.__getattribute__(other, '_storage')._slots[0] if isinstance(other, _SlotPtr) else other
        return v == ov

    def __lt__(self, other):
        v = object.__getattribute__(self, '_storage')._slots[0]
        ov = object.__getattribute__(other, '_storage')._slots[0] if isinstance(other, _SlotPtr) else other
        return v < ov

    def __le__(self, other):
        v = object.__getattribute__(self, '_storage')._slots[0]
        ov = object.__getattribute__(other, '_storage')._slots[0] if isinstance(other, _SlotPtr) else other
        return v <= ov

    def __gt__(self, other):
        v = object.__getattribute__(self, '_storage')._slots[0]
        ov = object.__getattribute__(other, '_storage')._slots[0] if isinstance(other, _SlotPtr) else other
        return v > ov

    def __ge__(self, other):
        v = object.__getattribute__(self, '_storage')._slots[0]
        ov = object.__getattribute__(other, '_storage')._slots[0] if isinstance(other, _SlotPtr) else other
        return v >= ov

    def __hash__(self):
        return hash(object.__getattribute__(self, '_storage')._slots[0])


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

    def capacity(self) -> int:
        return self._capacity

    def init_from_span(self, start: int, src) -> None:
        for i, val in enumerate(src):
            self._slots[start + i] = val

    def drop_n(self, start: int, count: int) -> None:
        for i in range(start, start + count):
            if i in self._slots:
                del self._slots[i]

    def shift(self, src: int, dst: int, count: int) -> None:
        if count <= 0 or src == dst:
            return
        items = [self._slots.pop(src + i) for i in range(count)]
        for i, val in enumerate(items):
            self._slots[dst + i] = val

    def ptr(self):
        from tpy import take_ptr
        return take_ptr(self)
