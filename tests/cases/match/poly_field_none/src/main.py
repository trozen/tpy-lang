# `field=None` on a @dynamic / polymorphic subject: the None check is emitted
# after the dynamic_cast, falling through to a bare `Dog()` arm when it fails.
from typing import Protocol
from tpy import dynamic


@dynamic
class Pet(Protocol):
    def speak(self) -> str: ...


class Dog(Pet):
    name: "str | None"
    def __init__(self, name: "str | None") -> None:
        self.name = name
    def speak(self) -> str:
        return "woof"


def describe(p: Pet) -> str:
    match p:
        case Dog(name=None):  # tpyc: ok
            return "nameless dog"
        case Dog():
            return "named dog"
        case _:
            return "?"


def main() -> None:
    print(describe(Dog(None)))
    print(describe(Dog("rex")))


main()
