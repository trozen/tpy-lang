# Error: the generic twin of a popping `Iterator[P]` generator rejects alike
# (docs/LANGUAGE_FEATURES.md, Reference preservation through generic generators).
from typing import Iterator
from tpy import int32


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def drain[T](xs: list[T]) -> Iterator[T]:
    while len(xs) > 0:
        yield xs.pop()  # tpyc: error(/Cannot yield local or temporary as reference.*Iterator\[Own\[T\]\]/)


def main() -> None:
    ps = [P(1)]
    for p in drain(ps):
        print(p.n)


main()
