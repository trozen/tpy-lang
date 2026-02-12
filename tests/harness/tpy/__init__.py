"""
TurboPython CPython simulation module.

This module provides Python implementations of TurboPython types,
allowing .tp.py files to run in CPython for testing purposes.
"""

from __future__ import annotations
from typing import Generic, TypeVar, get_args, get_origin
from collections.abc import Sized

T = TypeVar('T')


# Char maps to single-character string in Python
Char = str


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

    def __deepcopy__(self, memo):
        # Pointers shallow-copy: new Ptr wrapper pointing to same object
        # This matches C++ where copying T* gives another pointer to same object
        return _Ptr(self._obj)

    def __copy__(self):
        return _Ptr(self._obj)


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

    def __deepcopy__(self, memo):
        # Pointers shallow-copy: new ConstPtr wrapper pointing to same object
        return _ConstPtr(self._obj)

    def __copy__(self):
        return _ConstPtr(self._obj)


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


class _OwnMeta(type):
    """Metaclass to support Own[T] syntax."""

    def __getitem__(cls, item):
        return cls


class Own(metaclass=_OwnMeta):
    """Own[T] - owned/moved object. In CPython, just returns the object."""

    def __new__(cls, obj):
        return obj


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

    def __new__(cls, *args, **kwargs):
        instance = super().__new__(cls)
        # Initialize _data in __new__ so it's available even if subclass
        # doesn't call super().__init__() (mimics C++ behavior where base
        # class default ctor is automatically called)
        instance._data = []
        return instance

    def __init__(self, init=None):
        if init is not None:
            if len(init) > self._capacity:
                raise RuntimeError(f"StaticList initializer exceeds capacity {self._capacity}")
            self._data = list(init)

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

    def __iter__(self):
        return iter(self._data)

    def pop(self, index: int = None):
        """Remove and return element at index (default last)."""
        if len(self._data) == 0:
            raise RuntimeError("StaticList pop from empty list")
        if index is None:
            return self._data.pop()
        # Normalize negative index
        if index < 0:
            index += len(self._data)
        if index < 0 or index >= len(self._data):
            raise RuntimeError(f"pop index out of range")
        return self._data.pop(index)

    def clear(self) -> None:
        """Remove all elements."""
        self._data.clear()

    def extend(self, iterable) -> None:
        """Extend with elements from iterable."""
        for item in iterable:
            self.append(item)

    def insert(self, index: int, value) -> None:
        """Insert value at index."""
        if len(self._data) >= self._capacity:
            raise RuntimeError(f"StaticList capacity exceeded")
        # Clamp index like Python's list.insert
        if index < 0:
            index += len(self._data)
            if index < 0:
                index = 0
        elif index > len(self._data):
            index = len(self._data)
        self._data.insert(index, value)

    def remove(self, value) -> None:
        """Remove first occurrence of value."""
        try:
            self._data.remove(value)
        except ValueError:
            raise RuntimeError("list.remove(x): x not in list")

    def index(self, value) -> Int32:
        """Return index of first occurrence of value."""
        try:
            return Int32(self._data.index(value))
        except ValueError:
            raise RuntimeError("list.index(x): x not in list")

    def count(self, value) -> Int32:
        """Return number of occurrences of value."""
        return Int32(self._data.count(value))

    def reverse(self) -> None:
        """Reverse in place."""
        self._data.reverse()


class ArrayMeta(type):
    """Metaclass to support Array[T, N] syntax."""

    def __getitem__(cls, params):
        if not isinstance(params, tuple) or len(params) != 2:
            raise TypeError("Array requires [T, N] syntax")
        elem_type, size = params

        class BoundArray(Array):
            _elem_type = elem_type
            _size = size

        return BoundArray


class Array(metaclass=ArrayMeta):
    """Fixed-size array: Array[T, N] -> std::array<T, N>"""

    _elem_type = None
    _size = 0

    def __init__(self, data: list = None):
        if data is None:
            self._data = [self._elem_type() for _ in range(self._size)]
        else:
            if len(data) != self._size:
                raise ValueError(f"Array size mismatch: expected {self._size}, got {len(data)}")
            self._data = list(data)

    def get(self, index: int):
        """Get element at index."""
        if index < 0 or index >= self._size:
            raise RuntimeError(f"Array index out of bounds: {index}")
        return self._data[index]

    def set(self, index: int, value) -> None:
        """Set element at index."""
        if index < 0 or index >= self._size:
            raise RuntimeError(f"Array index out of bounds: {index}")
        self._data[index] = value

    def __getitem__(self, index: int):
        return self.get(index)

    def __setitem__(self, index: int, value) -> None:
        self.set(index, value)

    def size(self) -> Int32:
        return Int32(self._size)

    def __len__(self) -> int:
        return self._size

    def __iter__(self):
        return iter(self._data)


class SpanMeta(type):
    """Metaclass to support Span[T] syntax."""

    def __getitem__(cls, elem_type):
        class BoundSpan(Span):
            _elem_type = elem_type

        return BoundSpan


class Span(metaclass=SpanMeta):
    """Non-owning view: Span[T] -> std::span<T>"""

    _elem_type = None

    def __init__(self, data):
        if isinstance(data, (list, Array, StaticList)):
            if hasattr(data, '_data'):
                self._data = data._data
            else:
                self._data = data
        else:
            self._data = list(data)

    def get(self, index: int):
        """Get element at index."""
        if index < 0 or index >= len(self._data):
            raise RuntimeError(f"Span index out of bounds: {index}")
        return self._data[index]

    def __getitem__(self, index: int):
        return self.get(index)

    def size(self) -> Int32:
        return Int32(len(self._data))

    def __len__(self) -> int:
        return len(self._data)

    def __iter__(self):
        return iter(self._data)

    def __copy__(self):
        """Shallow copy: new Span pointing to same data (like std::span)."""
        new_span = object.__new__(type(self))
        new_span._data = self._data  # Same reference, not copied
        return new_span

    def __deepcopy__(self, memo):
        """Deep copy for Span is still shallow - it's a view type."""
        # Views copy shallowly: new Span wrapper pointing to same underlying data
        # This matches std::span semantics in C++
        new_span = object.__new__(type(self))
        new_span._data = self._data  # Same reference, not copied
        return new_span


def noalloc(func):
    """Decorator marking a function as no-allocation.

    In CPython simulation, this is a no-op.
    The compiler enforces this constraint at compile time.
    """
    return func


def readonly(func):
    """Decorator marking a function/method as readonly.

    In CPython simulation, this is a no-op.
    The compiler enforces this constraint at compile time.
    """
    return func


import copy as _copy_module

def copy(obj):
    """Explicit copy for ownership transfer.

    In CPython, uses copy.deepcopy() to match C++ by-value semantics.
    When C++ returns a container by value, it deep-copies all elements.
    In TurboPython, this marks the value as owned (by-value return).
    """
    return _copy_module.deepcopy(obj)


# NativeIterable protocol for CPython compatibility
from typing import Protocol as _Protocol, runtime_checkable as _runtime_checkable


@_runtime_checkable
class NativeIterable(_Protocol[T]):
    """Types that support C++ range-based for loops.

    In CPython, this matches any iterable type.
    The TurboPython compiler uses this for types with begin()/end().
    """
    def __iter__(self): ...


@_runtime_checkable
class NativeIterator(_Protocol[T]):
    """Types that produce values lazily via next().

    In CPython, this matches any iterable type (for-loop compatibility).
    The TurboPython compiler uses this for types with a next() -> Optional[T] method.
    """
    def __iter__(self): ...


@_runtime_checkable
class Comparable(_Protocol):
    """Protocol for types supporting comparison with <."""
    def __lt__(self, other: "Comparable") -> bool: ...


# Import hook to find .tp.py files (with .py fallback)
import sys
import os
from importlib.machinery import ModuleSpec
from importlib.util import spec_from_file_location


class TpyModuleFinder:
    """Import hook to find .tp.py files (with .py fallback).

    This allows TurboPython modules to be imported in CPython for testing.
    The hook is registered at the start of sys.meta_path to intercept imports.
    """

    def find_spec(self, name, path, target=None):
        # Don't intercept standard library or installed packages
        if name in sys.modules:
            return None

        # Search in path (or sys.path if path is None)
        search_paths = path if path else sys.path
        for dir_path in search_paths:
            if not isinstance(dir_path, str):
                continue
            # Prefer .tp.py, fallback to .py
            for ext in [".tp.py", ".py"]:
                file_path = os.path.join(dir_path, f"{name}{ext}")
                if os.path.isfile(file_path):
                    return spec_from_file_location(name, file_path,
                        submodule_search_locations=[])
        return None


# Register the import hook
sys.meta_path.insert(0, TpyModuleFinder())
