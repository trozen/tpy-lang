# FixStr[N] -- fixed-capacity string with stack-allocated storage.
# Stores up to N characters inline without heap allocation.
#
# TODO: construction from str/StrView literal (implicit coercion)
# TODO: __eq__ for comparison (with FixStr and str)
# TODO: __add__ for concatenation
# TODO: slicing (s[1:3])
# TODO: string methods (find, startswith, endswith, strip, etc.)
# TODO: c_str() for C interop (null-terminated)
from __future__ import annotations
from tpy import Int32, UInt32, Own, Ptr, Char, StrView, copy, readonly
from tpy.unsafe import unsafe_load, unsafe_str_view
from tpy.mem import UninitArrayStorage


class FixStrIter:
    _data: Ptr[readonly[Char]]
    _size: Int32
    _index: Int32

    def __init__(self, data: Ptr[readonly[Char]], size: Int32) -> None:
        self._data = data
        self._size = size
        self._index = 0

    def __next__(self) -> Char:
        if self._index < self._size:
            val = unsafe_load(self._data, UInt32(self._index))
            self._index += 1
            return val
        raise StopIteration


class FixStr[N: int]:
    _storage: UninitArrayStorage[Char, N]
    _size: Int32

    def __init__(self) -> None:
        self._storage = UninitArrayStorage[Char, N]()
        self._size = 0

    def __del__(self) -> None:
        for i in range(self._size):
            self._storage.drop(UInt32(i))

    def __copy__(self) -> Own[FixStr[N]]:
        result = FixStr[N]()
        for i in range(self._size):
            result.append(self._storage.load(UInt32(i)))
        return result

    def __move__(self, other: Own[FixStr[N]]) -> None:
        # The storage can't move itself (it has no liveness); the owner does.
        self._storage.relocate_from(other._storage, UInt32.trunc(other._size))
        self._size = other._size

    def append(self, c: Char) -> None:
        self._storage.init(UInt32(self._size), c)
        self._size += 1

    def pop(self) -> Char:
        self._size -= 1
        return self._storage.take(UInt32(self._size))

    def __len__(self) -> Int32:
        return self._size

    def __getitem__(self, index: Int32) -> Char:
        return self._storage.load(UInt32(index))

    def __setitem__(self, index: Int32, value: Char) -> None:
        self._storage.drop(UInt32(index))
        self._storage.init(UInt32(index), value)

    def __str__(self) -> StrView:
        return unsafe_str_view(self._storage.ptr(), UInt32(self._size))

    def __iter__(self) -> Own[FixStrIter]:
        return FixStrIter(self._storage.ptr(), self._size)

    def clear(self) -> None:
        for i in range(self._size):
            self._storage.drop(UInt32(i))
        self._size = 0
