# Regression: sync generator function with pointer-form Optional[NonValue]
# param. Frame slot + factory signature must use `T*` (matching sync
# functions and async coroutines), not the default `T&` shape -- otherwise
# the factory wants a `T&` but the body accesses via `->` (mismatch) and
# `None` callers fail to find a conversion.
from tpy import nocopy, int32
from typing import Iterator


@nocopy
class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def gen_n_times(p: P | None, n: int32) -> Iterator[int32]:
    for _ in range(n):
        if p is not None:
            yield p.n
        else:
            yield -1


def main() -> None:
    items: list[P] = []
    items.append(P(42))
    for v in gen_n_times(items[0], 3):
        print(v)
    for v in gen_n_times(None, 2):
        print(v)


main()
