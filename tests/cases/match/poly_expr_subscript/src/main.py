# Polymorphic match on a SUBSCRIPT subject (`match animals[i]:`): dispatched via
# dynamic_cast on the once-bound subject's Box deref view.
from typing import Protocol
from tpy import dynamic
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


def describe(animals: list[Box[Animal]], i: int) -> str:
    match animals[i]:  # tpyc: ok
        case Dog():
            return "dog"
        case Snake() as s:
            return "snake legs=" + str(s.legs)
        case _:
            return "?"


def main() -> None:
    animals: list[Box[Animal]] = []
    animals.append(Box(Dog()))
    animals.append(Box(Snake()))
    print(describe(animals, 0))
    print(describe(animals, 1))


main()
