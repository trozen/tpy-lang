"""
TurboPython CPython simulation module.

This module provides Python implementations of TurboPython types,
allowing .tp.py files to run in CPython for testing purposes.
"""

from __future__ import annotations
from typing import Generic, TypeVar, get_args, get_origin

T = TypeVar('T')


class Int32(int):
    """32-bit signed integer with overflow behavior."""

    MIN = -(2**31)
    MAX = 2**31 - 1

    def __new__(cls, value: int = 0) -> Int32:
        # Wrap on overflow (like C++ int32_t)
        value = int(value)
        if value < cls.MIN or value > cls.MAX:
            value = ((value - cls.MIN) % (2**32)) + cls.MIN
        return super().__new__(cls, value)

    def __add__(self, other: int) -> Int32:
        return Int32(super().__add__(other))

    def __radd__(self, other: int) -> Int32:
        return Int32(super().__radd__(other))

    def __sub__(self, other: int) -> Int32:
        return Int32(super().__sub__(other))

    def __rsub__(self, other: int) -> Int32:
        return Int32(super().__rsub__(other))

    def __mul__(self, other: int) -> Int32:
        return Int32(super().__mul__(other))

    def __rmul__(self, other: int) -> Int32:
        return Int32(super().__rmul__(other))

    def __floordiv__(self, other: int) -> Int32:
        return Int32(super().__floordiv__(other))

    def __mod__(self, other: int) -> Int32:
        return Int32(super().__mod__(other))

    def __neg__(self) -> Int32:
        return Int32(super().__neg__())

    def __repr__(self) -> str:
        return str(int(self))

    def __str__(self) -> str:
        return str(int(self))


class _Ptr(Generic[T]):
    """Mutable pointer simulation."""

    def __init__(self, obj: T):
        self._obj = obj

    def __getattr__(self, name: str):
        return getattr(self._obj, name)

    def __setattr__(self, name: str, value):
        if name.startswith('_'):
            super().__setattr__(name, value)
        else:
            setattr(self._obj, name, value)


class _ConstPtr(Generic[T]):
    """Read-only pointer simulation."""

    def __init__(self, obj: T):
        self._obj = obj

    @property
    def value(self):
        """Dereference to get the underlying value."""
        return self._obj

    def __getattr__(self, name: str):
        return getattr(self._obj, name)

    def __int__(self):
        return int(self._obj)

    def __index__(self):
        return int(self._obj)


class _PtrMeta(type):
    """Metaclass to support Ptr[T] syntax."""

    def __getitem__(cls, item):
        return cls


class Ptr(metaclass=_PtrMeta):
    """Ptr[T] type - creates mutable pointer."""

    def __new__(cls, obj):
        return _Ptr(obj)


class ConstPtr(metaclass=_PtrMeta):
    """ConstPtr[T] type - creates read-only pointer."""

    def __new__(cls, obj):
        return _ConstPtr(obj)


class StaticListMeta(type):
    """Metaclass to support StaticList[T, N] syntax."""

    def __getitem__(cls, params):
        if not isinstance(params, tuple) or len(params) != 2:
            raise TypeError("StaticList requires [T, N] syntax")
        elem_type, capacity = params

        class BoundStaticList(StaticList):
            _elem_type = elem_type
            _capacity = capacity

        return BoundStaticList


class StaticList(metaclass=StaticListMeta):
    """Fixed-capacity list with no dynamic allocation."""

    _elem_type = None
    _capacity = 0

    def __init__(self):
        self._data: list = []

    def append(self, value) -> None:
        if len(self._data) >= self._capacity:
            raise RuntimeError(f"StaticList overflow: capacity is {self._capacity}")
        self._data.append(value)

    def push_empty(self):
        """Reserve space and return pointer to the new element."""
        if len(self._data) >= self._capacity:
            raise RuntimeError(f"StaticList overflow: capacity is {self._capacity}")
        obj = self._elem_type()
        self._data.append(obj)
        return Ptr(obj)

    def get(self, index: int):
        """Get element at index (returns value directly)."""
        if index < 0 or index >= len(self._data):
            raise RuntimeError(f"StaticList index out of bounds: {index}")
        return self._data[index]

    def get_mut(self, index: int):
        """Get mutable pointer to element at index."""
        if index < 0 or index >= len(self._data):
            raise RuntimeError(f"StaticList index out of bounds: {index}")
        return Ptr(self._data[index])

    def set(self, index: int, value) -> None:
        """Set element at index."""
        if index < 0 or index >= len(self._data):
            raise RuntimeError(f"StaticList index out of bounds: {index}")
        self._data[index] = value

    def __setitem__(self, index: int, value) -> None:
        self.set(index, value)

    def __getitem__(self, index: int):
        return self.get(index)

    def size(self) -> Int32:
        return Int32(len(self._data))

    def __len__(self) -> int:
        return len(self._data)


def noalloc(func):
    """Decorator marking a function as no-allocation.

    In CPython simulation, this is a no-op.
    The compiler enforces this constraint at compile time.
    """
    return func
