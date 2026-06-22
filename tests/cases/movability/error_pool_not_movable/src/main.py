# A class owning UninitArrayStorage of a non-trivially-relocatable element
# (Item has a str field) with no __move__ is non-movable; relocating it (return
# of a named local) is a clean TPy error naming the field chain, not a raw C++
# deleted-move error.
from tpy import UInt32, Own
from tpy.mem import UninitArrayStorage


class Item:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name


class Pool:
    _storage: UninitArrayStorage[Item, 8]
    _size: UInt32

    def __init__(self) -> None:
        self._storage = UninitArrayStorage[Item, 8]()
        self._size = UInt32(0)

    def __del__(self) -> None:
        self._storage.drop_n(UInt32(0), self._size)


def make() -> Own[Pool]:
    p = Pool()
    return p  # tpyc: error(/Pool is not movable/)


def main() -> None:
    p = make()


main()
