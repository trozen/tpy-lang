# Polymorphic match on a CONST field subject: in a @readonly method `self.pet`
# is a const borrow, so the dynamic_cast must target `const Sub*` (const propagates).
from typing import Protocol
from tpy import dynamic, Own, readonly
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

    @readonly
    def describe(self) -> str:
        match self.pet:  # tpyc: ok
            case Dog():
                return "dog"
            case Snake() as s:
                return "snake legs=" + str(s.legs)
            case _:
                return "?"


def main() -> None:
    print(Owner(Box(Dog())).describe())
    print(Owner(Box(Snake())).describe())


main()
