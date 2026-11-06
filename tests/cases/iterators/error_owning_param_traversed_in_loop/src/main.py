# docs/LANGUAGE_FEATURES.md "An owning iterable is traversed once": a traversal
# that may run more than once -- a loop body, even one that runs once -- is an error.
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


def in_loop(it: Iterable[Own[R]]) -> int32:
    n = 0
    for _ in range(1):
        n += total(it)  # tpyc: error(/Cannot traverse 'it' inside a loop: an owning iterable \(Iterable\[Own\[R\]\]\) is traversed once/)
    return n


def main() -> None:
    print(in_loop([R(1), R(2)]))


main()
