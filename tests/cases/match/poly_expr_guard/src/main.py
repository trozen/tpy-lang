# Polymorphic match on a FIELD expression subject through the GUARDED path:
# a guard + an `as` capture + a literal field-value sub-pattern, none of which
# narrow the (nameless) subject -- the value is reached via the `as` binding.
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
    def __init__(self, legs: int) -> None:
        super().__init__(legs)


class Snake(Animal):
    def __init__(self) -> None:
        super().__init__(0)


class Owner:
    pet: Box[Animal]

    def __init__(self, pet: Own[Box[Animal]]) -> None:
        self.pet = pet


def describe(o: Owner) -> str:
    match o.pet:  # tpyc: ok
        case Snake() as s if s.legs == 0:
            return "legless snake"
        case Dog(legs=4):
            return "quadruped dog"
        case Dog() as d:
            return "dog legs=" + str(d.legs)
        case _:
            return "?"


def main() -> None:
    print(describe(Owner(Box(Snake()))))
    print(describe(Owner(Box(Dog(4)))))
    print(describe(Owner(Box(Dog(3)))))


main()
