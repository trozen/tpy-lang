# Generator method with a `yield` inside a `match`: same clean reject as
# the free-function shape.
from typing import Iterator


class Counter:
    n: int

    def __init__(self, n: int) -> None:
        self.n = n

    def items(self) -> Iterator[int]:
        match self.n:  # tpyc: error(/inside a .match. statement is not yet supported/)
            case 0:
                yield 10
                yield 20
            case _:
                yield 30


def main() -> None:
    c = Counter(0)
    for v in c.items():
        print(v)


main()
