# Multiple literal field arms on one polymorphic type, a field arm plus guard,
# and a bare-type catch-all -- a non-matching value falls through to the next arm.
from typing import Protocol
from tpy import dynamic


@dynamic
class Pet(Protocol):
    def speak(self) -> str: ...


class Dog(Pet):
    legs: int
    weight: int
    def __init__(self, legs: int, weight: int) -> None:
        self.legs = legs
        self.weight = weight
    def speak(self) -> str:
        return "woof"


def describe(p: Pet) -> str:
    match p:
        case Dog(legs=4) if p.weight > 10:  # tpyc: ok
            return "big quad dog"
        case Dog(legs=4):  # tpyc: ok
            return "small quad dog"
        case Dog(legs=3):  # tpyc: ok
            return "tripod dog"
        case Dog():
            return "odd dog"
        case _:
            return "not a dog"


def main() -> None:
    print(describe(Dog(4, 20)))
    print(describe(Dog(4, 5)))
    print(describe(Dog(3, 5)))
    print(describe(Dog(2, 5)))


main()
