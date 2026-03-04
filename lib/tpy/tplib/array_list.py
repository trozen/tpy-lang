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
from __future__ import annotations
from tpy import Int32, UInt32, Own, Ptr, ReadOnlySpan, Span, copy, Default, make_default
from tpy.mem import UninitArrayStorage


class ArrayList[T, N: int]:
    _storage: UninitArrayStorage[T, N]
    _size: Int32

    def __init__(self, items: ReadOnlySpan[T] | None = None) -> None:
        self._storage = UninitArrayStorage[T, N]()
        self._size = 0
        if items is not None:
            src = items
            for i in range(len(src)):
                self._storage.init(UInt32(self._size), copy(src[i]))
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
        return Span[T](self._storage.ptr(), self._size)

    def clear(self) -> None:
        for i in range(self._size):
            self._storage.drop(UInt32(i))
        self._size = 0
