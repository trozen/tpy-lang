"""
TurboPython unsafe pointer operations (tpy.unsafe).

CPython implementations of unsafe pointer operations. These simulate
the C++ pointer arithmetic that the compiler generates.
"""

from __future__ import annotations
from tpy import Ptr, ConstPtr


def unsafe_ptr(container):
    """Get a raw pointer from a container or string."""
    if isinstance(container, str):
        return ConstPtr(container)
    if isinstance(container, list):
        return Ptr(container)
    if hasattr(container, '_data'):
        return Ptr(container._data)
    return Ptr(container)


def unsafe_load(p, offset: int):
    """Read a value through a pointer at offset."""
    obj = p.__deref__() if hasattr(p, '__deref__') else p._obj
    return obj[offset]


def unsafe_store(p, offset: int, value) -> None:
    """Write a value through a pointer at offset."""
    obj = p.__deref__() if hasattr(p, '__deref__') else p._obj
    obj[offset] = value


def unsafe_copy_n(dest, src, count: int) -> None:
    """Copy N elements between pointers."""
    dest_obj = dest.__deref__() if hasattr(dest, '__deref__') else dest._obj
    src_obj = src.__deref__() if hasattr(src, '__deref__') else src._obj
    for i in range(count):
        dest_obj[i] = src_obj[i]


def unsafe_const_cast(p):
    """Remove const from a pointer (ConstPtr[T] -> Ptr[T])."""
    obj = p.__deref__() if hasattr(p, '__deref__') else p._obj
    return Ptr(obj)


def unsafe_ptr_add(p, delta: int):
    """Advance a pointer by a signed element offset."""
    # In CPython simulation, pointer arithmetic isn't meaningful
    # in the same way. Return a new pointer wrapper.
    return p


def unsafe_ptr_diff(p1, p2) -> int:
    """Distance between two pointers in elements."""
    return 0


def unsafe_cast(p):
    """Reinterpret a pointer as a different pointee type."""
    return p
