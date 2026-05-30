# Regression: a generator method on a TEMPORARY receiver must lift it into a
# loop-spanning local, else the frame's `const Box&` dangles. `clobber()`
# between construction and consumption forces a stale read to surface as garbage.
from typing import Iterator


class Box:
    a: int
    b: int
    c: int

    def __init__(self, a: int, b: int, c: int) -> None:
        self.a = a
        self.b = b
        self.c = c

    def vals(self) -> Iterator[int]:
        yield self.a
        yield self.b
        yield self.c


def clobber() -> int:
    x = 0
    for i in range(50):
        x = x + i * 7
    return x


def main() -> None:
    it = Box(11, 22, 33).vals()
    print(clobber())
    for v in it:
        print(v)


main()
