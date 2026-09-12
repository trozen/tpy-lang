# Test __iter__/__next__ protocol on generic classes with Ptr-typed fields.
from tpy import int32, uint32, Own, Ptr
from tpy.mem import UninitArrayStorage


class StorageIter[T, N: int]:
    _storage: Ptr[UninitArrayStorage[T, N]]
    _size: int32
    _index: int32

    def __init__(self, storage: Ptr[UninitArrayStorage[T, N]], size: int32) -> None:
        self._storage = storage
        self._size = size
        self._index = 0

    def __next__(self) -> Own[T]:
        if self._index < self._size:
            val = self._storage.load(uint32(self._index))
            self._index += 1
            return val
        raise StopIteration


class SimpleList[T, N: int]:
    _storage: UninitArrayStorage[T, N]
    _size: int32

    def __init__(self) -> None:
        self._storage = UninitArrayStorage[T, N]()
        self._size = 0

    def __del__(self) -> None:
        for i in range(self._size):
            self._storage.drop(uint32(i))

    def add(self, value: Own[T]) -> None:
        self._storage.init(uint32(self._size), value)
        self._size += 1

    def __iter__(self) -> Own[StorageIter[T, N]]:
        return StorageIter[T, N](self._storage, self._size)


def main() -> None:
    s = SimpleList[int32, 8]()
    s.add(10)
    s.add(20)
    s.add(30)

    for x in s:
        print(x)

    # iterate again
    for x in s:
        print(x)

main()
