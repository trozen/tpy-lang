# `match` over a POINTER-repr Optional inside a generator: only the
# value-repr optional dispatch is admitted at a suspension.
from typing import Iterator, Optional


class Rec:
    n: int

    def __init__(self, n: int) -> None:
        self.n = n


def gen(x: Optional[Rec]) -> Iterator[int]:  # tpyc: error(/stmt\.match/)
    # The subject is a pointer-repr Optional.
    match x:
        case None:
            yield -1
        case r:
            yield 1
            yield r.n


def main() -> None:
    for v in gen(Rec(3)):
        print(v)


main()
