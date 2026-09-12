# __getitem__ is implicitly @readonly -- mutating self inside it is rejected.
from tpy import int32

class CachedList:
    _data: list[int32]
    _hits: int32

    def __init__(self) -> None:
        self._data = [10, 20, 30]
        self._hits = 0

    def __getitem__(self, idx: int32) -> int32:
        self._hits = self._hits + 1  # tpyc: error(/Cannot mutate readonly reference/)
        return self._data[idx]
