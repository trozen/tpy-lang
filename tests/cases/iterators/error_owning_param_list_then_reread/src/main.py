# docs/LANGUAGE_FEATURES.md "An owning iterable is traversed once": `list(it)` is a
# traversal (a constructor whose parameter iterates), so a later loop over `it` is an error.
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
    xs = list(it)
    n = len(xs)
    for r in it:  # tpyc: error(/Cannot use 'it' after it was consumed: an owning iterable \(Iterable\[Own\[R\]\]\) is traversed once; copy it first: `xs = list\(it\)`/)
        n += r.n
    return n


def main() -> None:
    print(f([R(1), R(2)]))


main()
