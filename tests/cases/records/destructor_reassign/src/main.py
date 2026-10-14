# Every object a reassigned variable with __del__ held is destroyed exactly
# once: each rebind takes a slot of its own, which drops its object when the
# function returns or when a loop runs the same site again. Covers
# straight-line, loop, and conditional reassignment patterns. When an object
# drops (at the rebind under CPython, when its slot dies here) is no part of
# Python's semantics, so each section reads the drops sorted after the
# function has returned.
from tpy import Own, int32


DROPS: list[str] = []


class Resource:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name

    def __del__(self) -> None:
        DROPS.append(self.name)


def drops() -> Own[list[str]]:
    out = sorted(DROPS)
    DROPS.clear()
    return out


def test_straight() -> None:
    r = Resource("a")
    # straight: two rebinds, three objects, each dropped once.
    r = Resource("b")
    r = Resource("c")
    print("straight alive:", r.name)


def test_loop() -> None:
    r = Resource("init")
    for i in range(3):
        # loop: the site runs three times, dropping each previous object.
        r = Resource("loop")
    print("loop alive:", r.name)


def test_conditional(flag: int32) -> None:
    r = Resource("start")
    if flag > 0:
        # conditional: the rebind runs on one path only.
        r = Resource("branch")
    print("conditional alive:", r.name)


def main() -> None:
    test_straight()
    print("straight dropped:", drops())
    test_loop()
    print("loop dropped:", drops())
    test_conditional(1)
    print("conditional dropped:", drops())
    test_conditional(0)
    print("conditional dropped:", drops())


main()
