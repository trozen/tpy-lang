# Error: a lending `Iterator[T]` cannot hand out an `Own[T]` parameter
# (docs/LANGUAGE_FEATURES.md, Reference preservation through generic generators).
from typing import Iterator
from tpy import Own, int32


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def give[T](x: Own[T]) -> Iterator[T]:
    yield x  # tpyc: error(/Cannot yield local or temporary as reference.*Iterator\[Own\[T\]\]/)


def main() -> None:
    for q in give(P(1)):
        print(q.n)


main()
