# docs/LANGUAGE_FEATURES.md "An owning iterable is traversed once": a second
# traversal of an owning iterable parameter is an error at its line.
from typing import Iterable
from tpy import Own, int32


class R:
    def __init__(self, n: int32) -> None:
        self.n = n


def eat_twice(it: Iterable[Own[R]]) -> int32:
    n = 0
    for r in it:
        n += r.n
    for r in it:  # tpyc: error(/Cannot use 'it' after it was consumed: an owning iterable \(Iterable\[Own\[R\]\]\) is traversed once; copy it first: `xs = list\(it\)`/)
        n += r.n
    return n


def main() -> None:
    print(eat_twice([R(1), R(2)]))


main()
