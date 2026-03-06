# ArrayList[T, N] -- fixed-capacity list with stack-allocated uninitialized storage.
# Elements are placement-constructed on append and explicitly destroyed on pop/clear/__del__.
#
# TODO: construct with fixed-extent Span[T, N] (deduce both T and N from the argument)
# TODO: __contains__(value) -> bool (enables `x in list`)
# TODO: __eq__ (element-wise comparison)
# TODO: swap(i, j) -- O(1) element swap (3 moves vs take+take+init+init)
# TODO: truncate(new_len) -- drop tail elements via drop_n
# TODO: __repr__
from __future__ import annotations
from typing import MutableSequence, Iterable
from tpy import Int32, UInt32, Own, Ptr, ReadOnlySpan, Span, ReadOnlySpanLike, copy, Default, make_default, span
from tpy.mem import UninitArrayStorage


class ArrayList[T, N: int](ReadOnlySpanLike[T], MutableSequence[T]):
    _storage: UninitArrayStorage[T, N]
    _size: Int32

    def __init__(self, items: ReadOnlySpanLike[T] | Iterable[T] | None = None) -> None:
        self._storage = UninitArrayStorage[T, N]()
        self._size = 0
        if items is not None:
            self.extend(items)

    def __del__(self) -> None:
        self._storage.drop_n(0, UInt32.trunc(self._size))

    def __copy__(self) -> Own[ArrayList[T, N]]:
        result = ArrayList[T, N]()
        for ui in range(UInt32(self._size)):
            result.append(self._storage.load(ui))
        return result

    def append(self, value: Own[T]) -> None:
        assert self._size < self._storage.capacity()
        self._storage.init(UInt32.trunc(self._size), value)
        self._size += 1

    def append_default[T: Default](self) -> None:
        self.append(make_default())

    def pop(self, index: Int32 | None = None) -> Own[T]:
        assert self._size > 0
        if index is None:
            self._size -= 1
            return self._storage.take(UInt32.trunc(self._size))
        ui = UInt32.trunc(index)
        assert ui < self._size
        result = self._storage.take(ui)
        self._storage.shift(ui + 1, ui, UInt32.trunc(self._size - index - 1))
        self._size -= 1
        return result

    def insert(self, index: Int32, value: Own[T]) -> None:
        assert self._size < self._storage.capacity()
        ui = UInt32.trunc(index)
        assert ui <= self._size
        self._storage.shift(ui, ui + 1, UInt32.trunc(self._size - index))
        self._storage.init(ui, value)
        self._size += 1

    def index(self, value: T) -> Int32:
        for i in range(self._size):
            if self._storage.load(UInt32.trunc(i)) == value:
                return i
        return -1

    def count(self, value: T) -> Int32:
        n: Int32 = 0
        for ui in range(UInt32.trunc(self._size)):
            if self._storage.load(ui) == value:
                n += 1
        return n

    def remove(self, value: T) -> None:
        self.pop(self.index(value))

    def reverse(self) -> None:
        if self._size == 0:
            return
        lo = UInt32(0)
        hi = UInt32.trunc(self._size - 1)
        while lo < hi:
            a = self._storage.take(lo)
            b = self._storage.take(hi)
            self._storage.init(lo, b)
            self._storage.init(hi, a)
            lo += 1
            hi -= 1

    def __len__(self) -> Int32:
        return self._size

    def __getitem__(self, index: Int32) -> T:
        ui = UInt32.trunc(index)
        assert ui < self._size
        return self._storage.load(ui)

    def __setitem__(self, index: Int32, value: Own[T]) -> None:
        ui = UInt32.trunc(index)
        assert ui < self._size
        self._storage.drop(ui)
        self._storage.init(ui, value)

    def __delitem__(self, index: Int32) -> None:
        ui = UInt32.trunc(index)
        assert ui < self._size
        self._storage.drop(ui)
        self._storage.shift(ui + 1, ui, UInt32.trunc(self._size - index - 1))
        self._size -= 1

    def __span__(self) -> Span[T]:
        return self._storage.ptr().span(self._size)

    def extend(self, items: ReadOnlySpanLike[T] | Iterable[T]) -> None:
        if isinstance(items, ReadOnlySpanLike):
            items_span = span(items)
            size = len(items_span)
            assert self._size + size <= self._storage.capacity()
            self._storage.init_from_span(UInt32.trunc(self._size), items_span)
            self._size += size
        elif isinstance(items, Iterable):
            for item in items:
                self.append(copy(item))

    def clear(self) -> None:
        self._storage.drop_n(0, UInt32.trunc(self._size))
        self._size = 0

    def __str__(self) -> str:
        s = "["
        sep = False
        for item in self:
            if sep:
                s += ", "
            else:
                sep = True
            s += str(item)
        s += "]"
        return s
