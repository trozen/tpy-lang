# ArrayList[T, N] -- fixed-capacity list with stack-allocated uninitialized storage.
# Elements are placement-constructed on append and explicitly destroyed on pop/clear/__del__.
from __future__ import annotations
from tpy import Int32, UInt32, Own, Ptr, copy
from tpy.mem import UninitArrayStorage


# TODO: optimize to store Ptr[T] current + Ptr[T] end instead of storage + size + index
class ArrayListIter[T, N: int]:
    _storage: Ptr[UninitArrayStorage[T, N]]
    _size: Int32
    _index: Int32

    def __init__(self, storage: Ptr[UninitArrayStorage[T, N]], size: Int32) -> None:
        self._storage = storage
        self._size = size
        self._index = 0

    def __next__(self) -> Own[T]:
        if self._index < self._size:
            val = self._storage.load(UInt32(self._index))
            self._index += 1
            return val
        raise StopIteration


class ArrayList[T, N: int]:
    _storage: UninitArrayStorage[T, N]
    _size: Int32

    def __init__(self) -> None:
        self._storage = UninitArrayStorage[T, N]()
        self._size = 0

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

    # TODO: add pop(index) overload once per-method overloads are supported
    def pop(self) -> Own[T]:
        self._size -= 1
        return self._storage.take(UInt32(self._size))

    def pop_at(self, index: Int32) -> Own[T]:
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
        self.pop_at(self.index(value))

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

    def __iter__(self) -> Own[ArrayListIter[T, N]]:
        return ArrayListIter[T, N](Ptr(self._storage), self._size)

    def clear(self) -> None:
        for i in range(self._size):
            self._storage.drop(UInt32(i))
        self._size = 0
