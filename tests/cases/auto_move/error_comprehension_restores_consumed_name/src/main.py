# A comprehension rebinding a consumed local's name hides the consumption only
# while it binds the name: the enclosing local is still consumed after it.
from typing import Self
from tpy import Own, int32


class Box:
    xs: list[int32]

    def __init__(self, n: int32) -> None:
        self.xs = [n]

    def finish(self: Own[Self]) -> Own[Self]:
        return self


def test(ws: list[Box]) -> None:
    b = Box(9)
    old = b.finish()
    rs = [b.finish() for b in ws]
    print(len(old.xs), len(rs))
    print(b.xs)  # tpyc: error(/Cannot use 'b' after it was consumed/)


def main() -> None:
    test([Box(1)])


main()
