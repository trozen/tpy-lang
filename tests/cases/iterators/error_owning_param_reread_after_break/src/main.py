# docs/LANGUAGE_FEATURES.md "An owning iterable is traversed once": a `break` leaves
# the loop after its traversal, so the code after the loop reads a consumed name.
from typing import Iterable
from tpy import Own, int32


class R:
    def __init__(self, n: int32) -> None:
        self.n = n


def total(it: Iterable[Own[R]]) -> int32:
    n = 0
    for r in it:
        n += r.n
    return n


def f(it: Iterable[Own[R]], k: int32) -> int32:
    n = 0
    for i in range(k):
        if i == 1:
            n += total(it)
            break
    return n + total(it)  # tpyc: error(/Cannot use 'it' after it was consumed: an owning iterable/)


def main() -> None:
    print(f([R(1), R(2)], 3))


main()
