# ArrayList[T, N] -- fixed-capacity list with stack-allocated uninitialized storage.
# Elements are placement-constructed on append and explicitly destroyed on pop/clear/__del__.
#
# TODO: construct with fixed-extent Span[T, N] (deduce both T and N from the argument)
from __future__ import annotations
from typing import MutableSequence, Iterable
from tpy import int32, uint32, Own, Ptr, Span, Spannable, SpanIter, copy, Default, Comparable, Equatable, make_default, span, readonly, auto_readonly, basic_slice, dispatch
from tpy.mem import UninitArrayStorage


class ArrayList[T, N: int](Spannable[T], MutableSequence[T]):
    _storage: UninitArrayStorage[T, N]
    # _size carries an "always non-negative" invariant -- declared as uint32
    # so that internal comparisons against uint32 storage offsets and counters
    # don't require sign-mixed compares. The `__len__` boundary casts back to
    # int32 to match Python convention.
    _size: uint32

    def __init__(self, items: Spannable[T] | Iterable[Own[T]] | None = None) -> None:
        self._storage = UninitArrayStorage[T, N]()
        self._size = uint32(0)
        if items is not None:
            self.extend(items)

    def __del__(self) -> None:
        self._storage.drop_n(uint32(0), self._size)

    def __copy__(self) -> Own[ArrayList[T, N]]:
        result = ArrayList[T, N]()
        for ui in range(self._size):
            result.append(copy(self._storage.load(ui)))
        return result

    def __move__(self, other: Own[ArrayList[T, N]]) -> None:
        # The storage can't move itself (it has no liveness); the owner does.
        self._storage.relocate_from(other._storage, other._size)
        self._size = other._size

    def append(self, value: Own[T]) -> None:
        assert self._size < self._storage.capacity()
        self._storage.init(self._size, value)
        self._size += 1

    def append_default[T: Default](self) -> None:
        self.append(make_default())

    def pop(self, index: int32 | None = None) -> Own[T]:
        assert self._size > 0
        if index is None:
            self._size -= 1
            return self._storage.take(self._size)
        ui = uint32.trunc(index)
        assert ui < self._size
        result = self._storage.take(ui)
        self._storage.shift(ui + 1, ui, self._size - ui - 1)
        self._size -= 1
        return result

    def insert(self, index: int32, value: Own[T]) -> None:
        assert self._size < self._storage.capacity()
        ui = uint32.trunc(index)
        assert ui <= self._size
        self._storage.shift(ui, ui + 1, self._size - ui)
        self._storage.init(ui, value)
        self._size += 1

    def index[T: Equatable](self, value: T) -> int32:
        for ui in range(self._size):
            if self._storage.load(ui) == value:
                return int32.trunc(ui)
        assert False, "list.index(x): x not in list"

    def count[T: Equatable](self, value: T) -> int32:
        n: int32 = 0
        for ui in range(self._size):
            if self._storage.load(ui) == value:
                n += 1
        return n

    def remove[T: Equatable](self, value: T) -> None:
        self.pop(self.index(value))

    def reverse(self) -> None:
        if self._size == 0:
            return
        lo = uint32(0)
        hi = self._size - 1
        while lo < hi:
            a = self._storage.take(lo)
            b = self._storage.take(hi)
            self._storage.init(lo, b)
            self._storage.init(hi, a)
            lo += 1
            hi -= 1

    def swap(self, i: int32, j: int32) -> None:
        ui = uint32.trunc(i)
        uj = uint32.trunc(j)
        assert ui < self._size
        assert uj < self._size
        if ui == uj:
            return
        a = self._storage.take(ui)
        b = self._storage.take(uj)
        self._storage.init(ui, b)
        self._storage.init(uj, a)

    def sort[T: Comparable](self) -> None:
        self.__span__().sort()

    def truncate(self, new_len: int32) -> None:
        assert new_len >= 0
        u_new_len = uint32.trunc(new_len)
        if u_new_len < self._size:
            self._storage.drop_n(u_new_len, self._size - u_new_len)
            self._size = u_new_len

    def __len__(self) -> int32:
        return int32.trunc(self._size)

    @dispatch
    @auto_readonly
    def __getitem__(self, index: int32) -> auto_readonly[T]:
        ui = uint32.trunc(index)
        assert ui < self._size
        return self._storage.load(ui)

    @dispatch
    @auto_readonly
    def __getitem__(self, index: basic_slice) -> Span[auto_readonly[T]]:
        return self.__span__()[index]

    def __setitem__(self, index: int32, value: Own[T]) -> None:
        ui = uint32.trunc(index)
        assert ui < self._size
        self._storage.drop(ui)
        self._storage.init(ui, value)

    def __delitem__(self, index: int32) -> None:
        ui = uint32.trunc(index)
        assert ui < self._size
        self._storage.drop(ui)
        self._storage.shift(ui + 1, ui, self._size - ui - 1)
        self._size -= 1

    def __contains__[T: Equatable](self, value: T) -> bool:
        for ui in range(self._size):
            if self._storage.load(ui) == value:
                return True
        return False

    def __eq__[T: Equatable](self, other: ArrayList[T, N]) -> bool:
        if self._size != other._size:
            return False
        for ui in range(self._size):
            if self._storage.load(ui) != other._storage.load(ui):
                return False
        return True

    @auto_readonly
    def __span__(self) -> Span[auto_readonly[T]]:
        return self._storage.ptr().span(int32.trunc(self._size))

    @auto_readonly
    def __iter__(self) -> SpanIter[auto_readonly[T]]:
        return SpanIter(self.__span__())

    def extend(self, items: Spannable[T] | Iterable[Own[T]]) -> None:
        # TODO: warn for Spannable path too (also copies elements)
        if isinstance(items, Spannable):
            items_span = span(items)
            u_size = uint32.trunc(len(items_span))
            assert self._size + u_size <= self._storage.capacity()
            self._storage.init_from_span(self._size, items_span)
            self._size += u_size
        elif isinstance(items, Iterable):
            for item in items:
                self.append(copy(item))

    def clear(self) -> None:
        self._storage.drop_n(uint32(0), self._size)
        self._size = uint32(0)

    def __repr__(self) -> str:
        s = "["
        sep = False
        for item in self:
            if sep:
                s += ", "
            else:
                sep = True
            s += repr(item)
        s += "]"
        return s

    def __str__(self) -> str:
        return repr(self)
