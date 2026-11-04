# Error: a lending `Iterator[readonly[U]]` rejects a callable's result too
# (docs/LANGUAGE_FEATURES.md, Reference preservation through generic generators).
from typing import Iterable, Iterator
from tpy import Fn, Own, int32, readonly


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def ro_map[T, U](fn: Fn[[T], U], it: Iterable[T]) -> Iterator[readonly[U]]:
    for x in it:
        yield fn(x)  # tpyc: error(/Cannot yield local or temporary as reference.*`y = \.\.\.` then `yield y`/)


def mk(p: P) -> Own[P]:
    return P(p.n)


def main() -> None:
    for q in ro_map(mk, [P(1)]):
        print(q.n)


main()
