# A conditional `Dog(legs=4)` arm after an unconditional `Dog()` arm is dead:
# the bare arm already caught every Dog (the conditional arm is kept out of
# seen_poly, but the unreachable check still fires against the prior bare arm).
from typing import Protocol
from tpy import dynamic


@dynamic
class Pet(Protocol):
    def speak(self) -> str: ...


class Dog(Pet):
    legs: int

    def __init__(self, legs: int) -> None:
        self.legs = legs

    def speak(self) -> str:
        return "woof"


def describe(p: Pet) -> str:
    match p:
        case Dog():
            return "dog"
        case Dog(legs=4):  # tpyc: error(/unreachable case for 'Dog'/)
            return "quad"
        case _:
            return "?"


def main() -> None:
    print(describe(Dog(4)))


main()
