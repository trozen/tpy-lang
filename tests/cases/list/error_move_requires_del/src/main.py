# __move__ requires the class to also define __del__ (it reuses the
# moved-from drop flag that only __del__ classes carry).
from tpy import Int32, Own
from tpy.mem import UninitArrayStorage


class Pool:
    _storage: UninitArrayStorage[Int32, 4]
    _size: Int32

    def __init__(self) -> None:
        self._storage = UninitArrayStorage[Int32, 4]()
        self._size = 0

    def __move__(self, other: Own[Pool]) -> None:  # tpyc: error(/'__move__' requires the class to define '__del__'/)
        self._size = other._size


def main() -> None:
    p = Pool()
    print(p._size)


main()
