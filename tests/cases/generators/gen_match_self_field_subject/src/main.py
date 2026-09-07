# A generator matching a scalar field off `self`: the subject binds the
# frame's receiver member directly.
from typing import Iterator
from tpy import Int32


class Counter:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n

    def items(self) -> Iterator[Int32]:
        # The match subject is a scalar field read off `self`.
        match self.n:
            case 0:
                yield 10
                yield 20
            case _:
                yield 30
        self.n = 99


def main() -> None:
    c = Counter(0)
    for v in c.items():
        print(v)
    # The frame borrows the receiver, so the write above is visible here.
    print(c.n)
    # A CALL receiver is not a bare name, so it takes no view-iterable row.
    for v in Counter(1).items():
        print(v)


main()
