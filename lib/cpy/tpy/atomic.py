"""CPython implementation of tpy.atomic.

Single-threaded, so memory orderings are no-ops and the compare-exchange
forms never fail spuriously. Values are coerced to the element type so
fixed-width wrapping matches TPy's `std::atomic<T>`.
"""
from __future__ import annotations
from enum import Enum


class MemoryOrder(Enum):
    RELAXED = 0
    ACQUIRE = 1
    RELEASE = 2
    ACQ_REL = 3
    SEQ_CST = 4


class _AtomicMeta(type):
    def __getitem__(cls, params):
        class Bound(Atomic):
            _elem_type = params
        return Bound


class Atomic(metaclass=_AtomicMeta):
    _elem_type = None

    # Coerce every stored value to the element type so fixed-width wrapping
    # matches TPy's `std::atomic<T>` (the fixed-int classes wrap on construction
    # and in arithmetic). Without this, fetch_add/etc. would run on unbounded
    # Python ints and diverge from TPy on overflow.
    def _coerce(self, value):
        return self._elem_type(value) if self._elem_type is not None else value

    def __init__(self, value):
        self._v = self._coerce(value)

    def load(self, order=MemoryOrder.SEQ_CST):
        return self._v

    def store(self, value, order=MemoryOrder.SEQ_CST):
        self._v = self._coerce(value)

    def exchange(self, value, order=MemoryOrder.SEQ_CST):
        old = self._v
        self._v = self._coerce(value)
        return old

    def fetch_add(self, value, order=MemoryOrder.SEQ_CST):
        old = self._v
        self._v = self._coerce(old + value)
        return old

    def fetch_sub(self, value, order=MemoryOrder.SEQ_CST):
        old = self._v
        self._v = self._coerce(old - value)
        return old

    def fetch_and(self, value, order=MemoryOrder.SEQ_CST):
        old = self._v
        self._v = self._coerce(old & value)
        return old

    def fetch_or(self, value, order=MemoryOrder.SEQ_CST):
        old = self._v
        self._v = self._coerce(old | value)
        return old

    def fetch_xor(self, value, order=MemoryOrder.SEQ_CST):
        old = self._v
        self._v = self._coerce(old ^ value)
        return old

    def compare_exchange(self, expected, desired,
                         success=MemoryOrder.SEQ_CST, failure=MemoryOrder.SEQ_CST):
        if self._v == expected:
            observed = self._v  # the swapped-from value, before the store
            self._v = self._coerce(desired)
            return (True, observed)
        return (False, self._v)

    compare_exchange_weak = compare_exchange

    # In-place operators mutate and return self (Python rebinds `a = a.__iadd__`
    # to the same object). TPy lowers these to void in-place RMW; here they must
    # return self so the rebind is a no-op.
    def __iadd__(self, other):
        self._v = self._coerce(self._v + other)
        return self

    def __isub__(self, other):
        self._v = self._coerce(self._v - other)
        return self

    def __iand__(self, other):
        self._v = self._coerce(self._v & other)
        return self

    def __ior__(self, other):
        self._v = self._coerce(self._v | other)
        return self

    def __ixor__(self, other):
        self._v = self._coerce(self._v ^ other)
        return self

    def __str__(self):
        return f"Atomic({int(self._v)})"

    __repr__ = __str__


def fence(order=MemoryOrder.SEQ_CST):
    pass
