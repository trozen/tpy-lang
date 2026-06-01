# A field-value sub-pattern in a polymorphic-dispatch arm is rejected: the
# dynamic_cast test cannot also compare fields, so it would be silently
# dropped. Sema points at the guard form instead.
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
        case Dog(legs=4):  # tpyc: error(/field-value pattern on 'legs' is not supported/)
            return "quadruped"
        case _:
            return "?"


def main() -> None:
    print(describe(Dog(4)))


main()
