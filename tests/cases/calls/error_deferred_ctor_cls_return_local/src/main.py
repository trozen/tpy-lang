# A local returned by reference dangles (LANGUAGE_FEATURES "Own[T]"): a
# deferred `cls(xs)` is refused as its non-generic twin is.
from tpy import copy, int32


class W[T]:
    items: list[T]

    def __init__(self, xs: list[int32]) -> None:
        self.items = []

    def put(self, v: T) -> None:
        self.items.append(copy(v))

    @classmethod
    def make(cls, xs: list[int32]) -> "W[int32]":
        b = cls(xs)
        b.put(1)
        return b  # tpyc: error(/Cannot return local or temporary as reference/)


def main() -> None:
    print(len(W[int32].make([1]).items))


main()
