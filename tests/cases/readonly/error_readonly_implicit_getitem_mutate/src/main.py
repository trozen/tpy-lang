# __getitem__ is implicitly @readonly -- mutating self inside it is rejected.
from tpy import Int32

class CachedList:
    _data: list[Int32]
    _hits: Int32

    def __init__(self) -> None:
        self._data = [10, 20, 30]
        self._hits = 0

    def __getitem__(self, idx: Int32) -> Int32:
        self._hits = self._hits + 1  # tpyc: error(/Cannot mutate readonly reference/)
        return self._data[idx]
