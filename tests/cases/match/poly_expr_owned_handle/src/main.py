# Polymorphic match on the object a returned Own[Box[Animal]] holds: the box
# is bound by name first, since `match make_box(kind).get():` would borrow
# from a temporary.
from typing import Protocol
from tpy import dynamic, Own
from tplib import Box


@dynamic
class Tag(Protocol):
    pass


class Animal(Tag):
    legs: int

    def __init__(self, legs: int) -> None:
        self.legs = legs


class Dog(Animal):
    def __init__(self) -> None:
        super().__init__(4)


class Snake(Animal):
    def __init__(self) -> None:
        super().__init__(0)


def make_box(kind: int) -> Own[Box[Animal]]:
    if kind == 0:
        return Box(Dog())
    return Box(Snake())


def describe(kind: int) -> str:
    b = make_box(kind)
    match b.get():  # tpyc: ok
        case Dog():
            return "dog"
        case Snake() as s:
            return "snake legs=" + str(s.legs)
        case _:
            return "?"


def main() -> None:
    print(describe(0))
    print(describe(1))


main()
