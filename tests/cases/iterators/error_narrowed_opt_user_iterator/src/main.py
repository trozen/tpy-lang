# A narrowed pointer-Optional whose payload is a USER ITERATOR enters the same
# carve-out and must keep rejecting.
from tpy import Int32


class Counter:
    n: Int32

    def __init__(self) -> None:
        self.n = 0

    def __iter__(self) -> "Counter":
        return self

    def __next__(self) -> Int32 | None:
        self.n += 1
        if self.n > 3:
            return None
        return self.n


def total(r: Counter | None) -> Int32:
    n = 0
    if r is not None:
        for v in r:  # tpyc: error(/iter.user_iterator.name/)
            n = n + v
    return n


def main() -> None:
    print(total(Counter()), total(None))


main()
