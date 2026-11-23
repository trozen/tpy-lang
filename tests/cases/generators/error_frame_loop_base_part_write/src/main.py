# A frame kept open across loop passes is refused when the loop writes what it
# borrows through a base-part call (Base.m(self)), as through self.m().
from typing import Iterator


def walk(xs: list[int]) -> Iterator[int]:
    for x in xs:
        yield x


class B:
    xs: list[int]

    def __init__(self) -> None:
        self.xs = [1, 2]

    def grow(self) -> None:
        self.xs.append(3)


class D(B):
    def __init__(self) -> None:
        super().__init__()

    def g_base(self) -> Iterator[int]:
        for i in range(2):
            it = walk(self.xs)  # tpyc: error(/the loop calls 'grow', which may write 'self'/)
            B.grow(self)  # the base-part call is the write
            yield 1


def main() -> None:
    d = D()
    for x in d.g_base():
        print(x)


main()
