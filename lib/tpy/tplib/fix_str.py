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
from tpy import int32, uint32, Own, Ptr, char, StrView, copy, readonly
from tpy.unsafe import unsafe_load, unsafe_str_view
from tpy.mem import UninitArrayStorage


class FixStrIter:
    _data: Ptr[readonly[char]]
    _size: int32
    _index: int32

    def __init__(self, data: Ptr[readonly[char]], size: int32) -> None:
        self._data = data
        self._size = size
        self._index = 0

    def __next__(self) -> char:
        if self._index < self._size:
            val = unsafe_load(self._data, uint32(self._index))
            self._index += 1
            return val
        raise StopIteration


class FixStr[N: int]:
    _storage: UninitArrayStorage[char, N]
    _size: int32

    def __init__(self) -> None:
        self._storage = UninitArrayStorage[char, N]()
        self._size = 0

    def __del__(self) -> None:
        for i in range(self._size):
            self._storage.drop(uint32(i))

    def __copy__(self) -> Own[FixStr[N]]:
        result = FixStr[N]()
        for i in range(self._size):
            result.append(self._storage.load(uint32(i)))
        return result

    def __move__(self, other: Own[FixStr[N]]) -> None:
        # The storage can't move itself (it has no liveness); the owner does.
        self._storage.relocate_from(other._storage, uint32.trunc(other._size))
        self._size = other._size

    def append(self, c: char) -> None:
        self._storage.init(uint32(self._size), c)
        self._size += 1

    def pop(self) -> char:
        self._size -= 1
        return self._storage.take(uint32(self._size))

    def __len__(self) -> int32:
        return self._size

    def __getitem__(self, index: int32) -> char:
        return self._storage.load(uint32(index))

    def __setitem__(self, index: int32, value: char) -> None:
        self._storage.drop(uint32(index))
        self._storage.init(uint32(index), value)

    def __str__(self) -> StrView:
        return unsafe_str_view(self._storage.ptr(), uint32(self._size))

    def __iter__(self) -> Own[FixStrIter]:
        return FixStrIter(self._storage.ptr(), self._size)

    def clear(self) -> None:
        for i in range(self._size):
            self._storage.drop(uint32(i))
        self._size = 0
