# A nested def inside __init__ whose body pre-declares a rebind slot: the
# constructor is a fourth lowering entry point, so it needs the same lambda-hoist
# rejection the plain-function one has (THIR emitted the slot at the ctor's
# prologue, outside the lambda's capture list).
from tpy import int32


class Point:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x

    def bump(self) -> None:
        self.x += 1


class Holder:
    v: int32

    def __init__(self, k: int32) -> None:
        def inner(n: int32) -> int32:
            p = Point(n)
            alias = p
            p = Point(n * 100)
            alias.bump()
            return alias.x + p.x

        p = Point(1)
        if k > 0:
            p = Point(99)
        self.v = p.x + inner(k)


def main() -> None:
    h = Holder(2)
    print(h.v)


main()
