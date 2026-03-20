"""
TurboPython unsafe pointer operations (tpy.unsafe).

CPython implementations of unsafe pointer operations. These simulate
the C++ pointer arithmetic that the compiler generates.
"""

from __future__ import annotations
from tpy import Ptr, _ConstPtr, take_ptr


# ---------------------------------------------------------------------------
# Heap simulation helpers for unsafe_alloc / unsafe_alloc_n
# ---------------------------------------------------------------------------

class _HeapSlot:
    """Single-element heap slot. Delegates attribute access to stored value."""
    __slots__ = ('_value',)

    def __init__(self):
        object.__setattr__(self, '_value', None)

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, '_value'), name)

    def __setattr__(self, name, value):
        if name == '_value':
            object.__setattr__(self, name, value)
        else:
            setattr(object.__getattribute__(self, '_value'), name, value)

    def __getitem__(self, index):
        return object.__getattribute__(self, '_value')

    def __setitem__(self, index, value):
        object.__setattr__(self, '_value', value)

    def __eq__(self, other):
        v = object.__getattribute__(self, '_value')
        ov = object.__getattribute__(other, '_value') if isinstance(other, _HeapSlot) else other
        return v == ov

    def __lt__(self, other):
        v = object.__getattribute__(self, '_value')
        ov = object.__getattribute__(other, '_value') if isinstance(other, _HeapSlot) else other
        return v < ov

    def __le__(self, other):
        v = object.__getattribute__(self, '_value')
        ov = object.__getattribute__(other, '_value') if isinstance(other, _HeapSlot) else other
        return v <= ov

    def __gt__(self, other):
        v = object.__getattribute__(self, '_value')
        ov = object.__getattribute__(other, '_value') if isinstance(other, _HeapSlot) else other
        return v > ov

    def __ge__(self, other):
        v = object.__getattribute__(self, '_value')
        ov = object.__getattribute__(other, '_value') if isinstance(other, _HeapSlot) else other
        return v >= ov

    def __hash__(self):
        return hash(object.__getattribute__(self, '_value'))


class _HeapArray:
    """Multi-element heap storage. Delegates attribute access to element 0."""
    __slots__ = ('_slots',)

    def __init__(self, count):
        object.__setattr__(self, '_slots', [None] * count)

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, '_slots')[0], name)

    def __setattr__(self, name, value):
        if name == '_slots':
            object.__setattr__(self, name, value)
        else:
            setattr(object.__getattribute__(self, '_slots')[0], name, value)

    def __getitem__(self, index):
        return object.__getattribute__(self, '_slots')[index]

    def __setitem__(self, index, value):
        object.__getattribute__(self, '_slots')[index] = value


class _HeapArrayView:
    """Offset view into a _HeapArray. Created by unsafe_ptr_add."""
    __slots__ = ('_slots', '_offset')

    def __init__(self, slots, offset):
        object.__setattr__(self, '_slots', slots)
        object.__setattr__(self, '_offset', offset)

    def __getattr__(self, name):
        slots = object.__getattribute__(self, '_slots')
        off = object.__getattribute__(self, '_offset')
        return getattr(slots[off], name)

    def __setattr__(self, name, value):
        if name in ('_slots', '_offset'):
            object.__setattr__(self, name, value)
        else:
            slots = object.__getattribute__(self, '_slots')
            off = object.__getattribute__(self, '_offset')
            setattr(slots[off], name, value)

    def __getitem__(self, index):
        slots = object.__getattribute__(self, '_slots')
        off = object.__getattribute__(self, '_offset')
        return slots[off + index]

    def __setitem__(self, index, value):
        slots = object.__getattribute__(self, '_slots')
        off = object.__getattribute__(self, '_offset')
        slots[off + index] = value


# ---------------------------------------------------------------------------
# Pointer operations
# ---------------------------------------------------------------------------

def unsafe_ptr(container):
    """Get a raw pointer from a container or string."""
    if isinstance(container, str):
        return _ConstPtr(container)
    if isinstance(container, list):
        return take_ptr(container)
    if hasattr(container, '_data'):
        return take_ptr(container._data)
    return take_ptr(container)


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
    """Remove const from a pointer (Ptr[readonly[T]] -> Ptr[T])."""
    obj = p.__deref__() if hasattr(p, '__deref__') else p._obj
    return take_ptr(obj)


def unsafe_ptr_add(p, delta: int):
    """Advance a pointer by a signed element offset."""
    obj = p.__deref__() if hasattr(p, '__deref__') else p._obj
    if isinstance(obj, _HeapArray):
        return take_ptr(_HeapArrayView(object.__getattribute__(obj, '_slots'), int(delta)))
    if isinstance(obj, _HeapArrayView):
        slots = object.__getattribute__(obj, '_slots')
        off = object.__getattribute__(obj, '_offset')
        return take_ptr(_HeapArrayView(slots, off + int(delta)))
    return p


def unsafe_ptr_diff(p1, p2) -> int:
    """Distance between two pointers in elements."""
    return 0


def unsafe_cast(p):
    """Reinterpret a pointer as a different pointee type."""
    return p


class _SubscriptableAlloc:
    """Callable that also supports subscript syntax: unsafe_alloc[T]()."""
    def __call__(self):
        return take_ptr(_HeapSlot())
    def __getitem__(self, type_arg):
        return self

unsafe_alloc = _SubscriptableAlloc()


class _SubscriptableAllocN:
    """Callable that also supports subscript syntax: unsafe_alloc_n[T](count)."""
    def __call__(self, count: int):
        return take_ptr(_HeapArray(int(count)))
    def __getitem__(self, type_arg):
        return self

unsafe_alloc_n = _SubscriptableAllocN()


def unsafe_free(p) -> None:
    """Free raw memory allocated by unsafe_alloc/unsafe_alloc_n."""
    pass


def unsafe_init(p, value) -> None:
    """Placement-new: construct an object at a pointer location."""
    obj = p.__deref__() if hasattr(p, '__deref__') else p._obj
    obj[0] = value


def unsafe_move_out(p):
    """Move a value out of a pointer location without calling destructor."""
    obj = p.__deref__() if hasattr(p, '__deref__') else p._obj
    val = obj[0]
    obj[0] = None
    return val


def unsafe_drop(p) -> None:
    """Call destructor on an object at a pointer location."""
    obj = p.__deref__() if hasattr(p, '__deref__') else p._obj
    obj[0] = None


def unsafe_str_view(p, size: int) -> str:
    """Create a string view from a Ptr[Char] and length."""
    obj = p.__deref__() if hasattr(p, '__deref__') else p._obj
    return ''.join(obj[i] for i in range(size))
