# Parenthesized or-pattern groups on a @dynamic protocol subject: the
# alternatives lower to a chain of dynamic_cast tests, which requires the
# group to have been flattened into terminal class patterns.
from typing import Protocol
from tpy import dynamic


@dynamic
class Pet(Protocol):
    def speak(self) -> str: ...


class Dog(Pet):
    def __init__(self) -> None:
        pass

    def speak(self) -> str:
        return "woof"


class Cat(Pet):
    def __init__(self) -> None:
        pass

    def speak(self) -> str:
        return "meow"


class Hamster(Pet):
    def __init__(self) -> None:
        pass

    def speak(self) -> str:
        return "squeak"


def describe(p: Pet) -> str:
    match p:
        # Same alternatives as the flat `case Dog() | Cat() | Hamster():`.
        case (Dog() | Cat()) | Hamster():
            return "pet:" + p.speak()
        case _:
            return "?"


def main() -> None:
    print(describe(Dog()))
    print(describe(Cat()))
    print(describe(Hamster()))


main()
