# A generic pool over the @nomove UninitArrayStorage is non-movable for ANY
# element, even a trivially-copyable one (Int32): the raw storage can't relocate
# itself, so there is no element-conditional free move -- movability requires
# __move__ regardless of element triviality. Movability is re-evaluated per
# instantiation, so the diagnostic names the concrete Pool[Int32, 4].
from tpy import Int32, UInt32, Own
from tpy.mem import UninitArrayStorage


class Pool[T, N: int]:
    _storage: UninitArrayStorage[T, N]
    _size: UInt32

    def __init__(self) -> None:
        self._storage = UninitArrayStorage[T, N]()
        self._size = UInt32(0)

    def __del__(self) -> None:
        self._storage.drop_n(UInt32(0), self._size)


def make() -> Own[Pool[Int32, 4]]:
    p = Pool[Int32, 4]()
    return p  # tpyc: error(/Pool\[Int32, 4\] is not movable/)


def main() -> None:
    pass


main()
