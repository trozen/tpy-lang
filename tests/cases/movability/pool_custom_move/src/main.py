# A user-defined pool over UninitArrayStorage of a non-trivially-relocatable
# element (Item has a str field) is movable because it defines __move__; the
# movability trait recognizes the escape, and the forced (non-elided) move
# `relocated = pool` runs __move__ and relocates the live elements.
from __future__ import annotations
from tpy import int32, uint32, Own, readonly
from tpy.mem import UninitArrayStorage


class Item:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name


class Pool[T, N: int]:
    _storage: UninitArrayStorage[T, N]
    _size: uint32

    def __init__(self) -> None:
        self._storage = UninitArrayStorage[T, N]()
        self._size = uint32(0)

    def __del__(self) -> None:
        self._storage.drop_n(uint32(0), self._size)

    def __move__(self, other: Own[Pool[T, N]]) -> None:
        for ui in range(other._size):
            self._storage.init(ui, other._storage.take(ui))
        self._size = other._size

    def push(self, value: Own[T]) -> None:
        self._storage.init(self._size, value)
        self._size += 1

    @readonly
    def get(self, i: int32) -> readonly[T]:
        return self._storage.load(uint32.trunc(i))


class Tagged:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __del__(self) -> None:
        pass

    def __move__(self, other: Own[Tagged]) -> None:
        # `__move__` is inlined into a noexcept move ctor, so sema rejects a
        # raise that could escape it -- one a local handler catches cannot.
        try:
            raise ValueError("guarded")  # tpyc: ok
        except ValueError:
            pass
        self.n = other.n


def make() -> Own[Pool[Item, 4]]:
    p = Pool[Item, 4]()
    p.push(Item("alpha"))
    p.push(Item("beta"))
    return p


def main() -> None:
    pool = make()
    relocated = pool          # forced last-use move -> runs Pool.__move__
    print(relocated.get(0).name, relocated.get(1).name)  # alpha beta
    tag = Tagged(7)
    moved = tag               # forced last-use move -> runs Tagged.__move__
    print("move_raise_guarded", moved.n)


main()
