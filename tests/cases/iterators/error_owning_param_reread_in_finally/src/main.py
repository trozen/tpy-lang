# docs/LANGUAGE_FEATURES.md "An owning iterable is traversed once": a traversal on
# a returning branch of a `try` body has run when its `finally` reads the parameter again.
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


def f(it: Iterable[Own[R]]) -> int32:
    n = 0
    try:
        if n == 0:
            return total(it)
    finally:
        n += total(it)  # tpyc: error(/Cannot use 'it' after it was consumed: an owning iterable/)
    return n


def main() -> None:
    print(f([R(1), R(2)]))


main()
