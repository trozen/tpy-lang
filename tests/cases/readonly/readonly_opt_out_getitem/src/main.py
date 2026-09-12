# @readonly(False) on __getitem__ opts out of implicit readonly,
# allowing mutation (e.g. cache hit counter) inside the indexer.
from tpy import int32, readonly

class CachedList:
    _data: list[int32]
    _hits: int32

    def __init__(self) -> None:
        self._data = [10, 20, 30]
        self._hits = 0

    @readonly(False)
    def __getitem__(self, idx: int32) -> int32:
        self._hits = self._hits + 1
        return self._data[idx]

c = CachedList()
print(c[0])
print(c[1])
