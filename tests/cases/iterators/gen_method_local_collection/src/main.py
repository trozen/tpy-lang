# A generator METHOD with a collection literal assigned to a local (hoisted
# into the frame). Exercises the method-side generator_locals collection path:
# the deferred element type must resolve before the frame field is rendered.
from typing import Iterator
from tpy import Int32


class Series:
    a: Int32
    b: Int32

    def __init__(self, a: Int32, b: Int32) -> None:
        self.a = a
        self.b = b

    def items(self) -> Iterator[Int32]:
        tmp = [self.a, self.b, self.a + self.b]
        for x in tmp:
            yield x
            yield x


def main() -> None:
    se = Series(3, 4)
    for v in se.items():
        print(v)


main()
