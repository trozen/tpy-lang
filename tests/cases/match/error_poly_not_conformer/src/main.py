# A class pattern naming a type that neither inherits nor structurally
# conforms to the @dynamic protocol can never match -- sema rejects it
# rather than emitting a dynamic_cast that always fails.
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


class Widget:
    def __init__(self) -> None:
        pass

    def render(self) -> str:
        return "widget"


def describe(p: Pet) -> str:
    match p:
        case Dog():
            return "dog"
        case Widget():  # tpyc: error(/does not conform to the @dynamic protocol 'Pet'/)
            return "widget"
        case _:
            return "?"


def main() -> None:
    print(describe(Dog()))


main()
