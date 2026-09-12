# A rebind slot reserved while emitting a nested def's lambda body must be
# declared inside that lambda: the enclosing function's prologue is outside the
# lambda's capture list, so a slot declared there is not visible.
from tpy import int32


class Point:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x

    def bump(self) -> None:
        self.x += 1


def outer(flag: int32) -> int32:
    def inner(k: int32) -> int32:
        p = Point(k)
        if k > 0:
            p = Point(k * 10)
        return p.x

    def inner_alias(k: int32) -> int32:
        p = Point(k)
        alias = p
        p = Point(k * 100)  # tpyc: ok
        alias.bump()
        return alias.x + p.x

    p = Point(1)
    if flag > 0:
        p = Point(99)
    return p.x + inner(flag) + inner_alias(flag)


def main() -> None:
    print(outer(1))
    print(outer(0))


main()
