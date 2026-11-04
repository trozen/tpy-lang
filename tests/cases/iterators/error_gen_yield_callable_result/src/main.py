# Error: a lending `Iterator[U]` cannot hand out a callable value's result
# (docs/LANGUAGE_FEATURES.md, Reference preservation through generic generators).
from typing import Iterable, Iterator
from tpy import Fn, int32


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def my_map[T, U](fn: Fn[[T], U], it: Iterable[T]) -> Iterator[U]:
    for x in it:
        yield fn(x)  # tpyc: error(/Cannot yield local or temporary as reference.*`y = \.\.\.` then `yield y`.*Iterator\[Own\[U\]\]/)


def identity(p: P) -> P:
    return p


def main() -> None:
    for q in my_map(identity, [P(1)]):
        print(q.n)


main()
