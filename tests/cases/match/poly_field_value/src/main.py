# A literal field sub-pattern on a @dynamic / polymorphic arm (`Dog(legs=4)`)
# compares the field after the dynamic_cast, falling through to a bare `Dog()`.
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


class Cat(Pet):
    def __init__(self) -> None:
        pass
    def speak(self) -> str:
        return "meow"


def describe(p: Pet) -> str:
    match p:
        case Dog(legs=4):  # tpyc: ok
            return "quadruped dog"
        case Dog():
            return "other dog"
        case _:
            return "not a dog"


def main() -> None:
    print(describe(Dog(4)))
    print(describe(Dog(3)))
    print(describe(Cat()))


main()
