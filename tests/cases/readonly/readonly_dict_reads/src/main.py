# Reading a dict field from auto-readonly methods (self._data is
# readonly[dict[T,int]]): .get(key, default), `in`, for-iteration, d[key].
from typing import Iterable
from tpy import Hashable


class Bag[T: Hashable]:
    _data: dict[T, int]

    def __init__(self) -> None:
        self._data = {}

    def add_all(self, items: Iterable[T]) -> None:
        for x in items:
            self._data[x] = self._data.get(x, 0) + 1  # tpyc: ok

    # readonly methods (no self mutation) -> self._data is readonly[dict]
    def __getitem__(self, key: T) -> int:
        return self._data.get(key, 0)  # tpyc: ok

    def __contains__(self, key: T) -> bool:
        return key in self._data  # tpyc: ok

    def total(self) -> int:
        s = 0
        for k in self._data:
            s = s + self._data[k]  # tpyc: ok  -- readonly key from iterating readonly self
        return s


def main() -> None:
    b = Bag[str]()
    xs = ["a", "b", "a", "c", "a", "b"]
    b.add_all(xs)
    print(b["a"], b["b"], b["z"])
    print("a" in b, "z" in b)
    print(b.total())


main()
