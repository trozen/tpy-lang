# Polymorphic match on a FIELD subject (`match o.pet:`, a Box[Animal] field):
# arms dispatch on the deref payload's dynamic type; an `as` capture binds it.
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


class Owner:
    pet: Box[Animal]

    def __init__(self, pet: Own[Box[Animal]]) -> None:
        self.pet = pet


def describe(o: Owner) -> str:
    match o.pet:  # tpyc: ok
        case Dog():
            return "dog"
        case Snake() as s:
            return "snake legs=" + str(s.legs)
        case _:
            return "?"


def main() -> None:
    print(describe(Owner(Box(Dog()))))
    print(describe(Owner(Box(Snake()))))


main()
