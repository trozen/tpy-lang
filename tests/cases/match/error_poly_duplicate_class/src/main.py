# Two arms naming the same subclass in a polymorphic match are a
# duplicate case -- the second can never be reached.
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


def describe(p: Pet) -> str:
    match p:
        case Dog():
            return "dog1"
        case Dog():  # tpyc: error(/duplicate case for 'Dog'/)
            return "dog2"
        case _:
            return "?"


def main() -> None:
    print(describe(Dog()))


main()
