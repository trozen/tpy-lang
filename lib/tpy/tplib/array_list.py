# ArrayList[T, N] -- fixed-capacity list with stack-allocated uninitialized storage.
# Elements are placement-constructed on append and explicitly destroyed on pop/clear/__del__.
#
# TODO: to fully replace builtin StaticList[T, N]:
# - Constructor: accept items: Iterable[T] | None = None. Inside the body, use
#   isinstance(items, Sized) to detect sized inputs (Span, list) and preallocate.
#   Needs: Optional[StaticProtocol] codegen (B14), isinstance on static protocols (B15).
#   Future: with fixed-extent Span[T, N], the constructor could accept Span[T, N]
#   (same N as the class) and deduce both T and N from the argument, enabling
#   `ArrayList(span)` without explicit type args. Needs: Span[T, N] type, constructor
#   type arg inference from arguments, int type param deduction.
# TODO: __str__/__repr__?
# TODO: bounds checks for append, insert, __init__
# TODO: emptiness check for pop
from __future__ import annotations
from typing import MutableSequence, Iterable
from tpy import Int32, UInt32, Own, Ptr, ReadOnlySpan, Span, ReadOnlySpanLike, copy, Default, make_default, span
from tpy.mem import UninitArrayStorage


class ArrayList[T, N: int](ReadOnlySpanLike[T], MutableSequence[T]):
    _storage: UninitArrayStorage[T, N]
    _size: Int32

    # TODO: error: Iterable used without importing
    def __init__(self, items: ReadOnlySpanLike[T] | Iterable[T] | None = None) -> None:
        self._storage = UninitArrayStorage[T, N]()
        self._size = 0
        if isinstance(items, ReadOnlySpanLike):
            # TODO: memcpy for primitive/trivial types
            items_span = span(items)
            size = len(items_span)
            for i in range(size):
                self._storage.init(UInt32(i), copy(items_span[i]))
            self._size = size
        elif isinstance(items, Iterable):
            for item in items:
                self._storage.init(UInt32(self._size), copy(item))
                self._size += 1

    def __del__(self) -> None:
        for i in range(self._size):
            self._storage.drop(UInt32(i))

    def __copy__(self) -> Own[ArrayList[T, N]]:
        result = ArrayList[T, N]()
        for i in range(self._size):
            result.append(self._storage.load(UInt32(i)))
        return result

    def append(self, value: Own[T]) -> None:
        self._storage.init(UInt32(self._size), value)
        self._size += 1

    def append_default[T: Default](self) -> None:
        self._storage.init(UInt32(self._size), make_default())
        self._size += 1

    def pop(self, index: Int32 | None = None) -> Own[T]:
        if index is None:
            self._size -= 1
            return self._storage.take(UInt32(self._size))
        result = self._storage.take(UInt32(index))
        i = index
        end = self._size - 1
        while i < end:
            self._storage.init(UInt32(i), self._storage.take(UInt32(i + 1)))
            i += 1
        self._size -= 1
        return result

    def insert(self, index: Int32, value: Own[T]) -> None:
        i = self._size - 1
        while i >= index:
            self._storage.init(UInt32(i + 1), self._storage.take(UInt32(i)))
            i -= 1
        self._storage.init(UInt32(index), value)
        self._size += 1

    def index(self, value: T) -> Int32:
        for i in range(self._size):
            if self._storage.load(UInt32(i)) == value:
                return i
        return -1

    def count(self, value: T) -> Int32:
        n: Int32 = 0
        for i in range(self._size):
            if self._storage.load(UInt32(i)) == value:
                n += 1
        return n

    def remove(self, value: T) -> None:
        self.pop(self.index(value))

    def reverse(self) -> None:
        lo: Int32 = 0
        hi = self._size - 1
        while lo < hi:
            a = self._storage.take(UInt32(lo))
            b = self._storage.take(UInt32(hi))
            self._storage.init(UInt32(lo), b)
            self._storage.init(UInt32(hi), a)
            lo += 1
            hi -= 1

    def __len__(self) -> Int32:
        return self._size

    def __getitem__(self, index: Int32) -> T:
        return self._storage.load(UInt32(index))

    def __setitem__(self, index: Int32, value: Own[T]) -> None:
        self._storage.drop(UInt32(index))
        self._storage.init(UInt32(index), value)

    def __delitem__(self, index: Int32) -> None:
        self._storage.drop(UInt32(index))
        i = index
        end = self._size - 1
        while i < end:
            self._storage.init(UInt32(i), self._storage.take(UInt32(i + 1)))
            i += 1
        self._size -= 1

    def __span__(self) -> Span[T]:
        return self._storage.ptr().span(self._size)

    def clear(self) -> None:
        for i in range(self._size):
            self._storage.drop(UInt32(i))
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
