# A `for` over a readonly source is an implicit `__iter__` call: one that
# mutates its receiver is rejected like `g.method()` would be.
from tpy import int32, readonly


class Counter:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def __iter__(self) -> "Counter":
        self.n = 0
        return self

    def __next__(self) -> int32:
        self.n += 1
        if self.n > 3:
            raise StopIteration()
        return self.n


def total(c: readonly[Counter]) -> int32:
    t = 0
    for v in c:  # tpyc: error(/non-readonly method '__iter__' on readonly reference/)
        t += v
    return t


def main() -> None:
    print(total(Counter()))


main()
