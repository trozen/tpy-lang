# Polymorphic dispatch narrows the subject through a dynamic_cast on its
# original storage, which (like isinstance) needs a stable variable name.
# A non-name subject (here a subscript yielding a polymorphic value) is
# rejected with a bind-to-local hint.
from typing import Protocol
from tpy import dynamic


@dynamic
class Tag(Protocol):
    pass


class Animal(Tag):
    def __init__(self) -> None:
        pass


class Dog(Animal):
    def __init__(self) -> None:
        pass


def describe(animals: list[Animal]) -> str:
    match animals[0]:  # tpyc: error(/requires the subject to be a variable name/)
        case Dog():
            return "dog"
        case _:
            return "?"


def main() -> None:
    print(describe([Dog()]))


main()
