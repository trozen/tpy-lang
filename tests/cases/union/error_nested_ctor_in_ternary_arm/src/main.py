# A nested member ctor inside a DEFERRED ternary arm: the union lift would have
# to hoist eager temps into a conditionally-evaluated arm, so it is rejected.
from typing import Optional
from tpy import Int32


class Collar:
    size: Int32

    def __init__(self, size: Int32) -> None:
        self.size = size


class Dog:
    collar: Optional[Collar]

    def __init__(self, c: Optional[Collar]) -> None:
        self.collar = c


class Cat:
    tag: Int32

    def __init__(self, tag: Int32) -> None:
        self.tag = tag


def check(a: Dog | Cat) -> bool:
    return True


def pick(x: bool) -> bool:
    return check(Dog(Collar(2))) if x else False  # tpyc: error(/unionlift.cond_defer/)


def main() -> None:
    print(pick(True))


main()
