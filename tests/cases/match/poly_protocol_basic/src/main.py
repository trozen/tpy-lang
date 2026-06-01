# match/case polymorphic dispatch on a bare @dynamic protocol: class
# patterns lower to dynamic_cast, the subject narrows to the matched
# subclass inside the arm (so `p.speak()` virtual-dispatches), and field
# bindings / or-patterns / wildcard all work.
from typing import Protocol
from tpy import dynamic


@dynamic
class Pet(Protocol):
    def speak(self) -> str: ...


class Dog(Pet):
    name: str

    def __init__(self, name: str) -> None:
        self.name = name

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
    match p:  # tpyc: ok
        case Dog(name=n):
            return "dog:" + n + ":" + p.speak()
        case Cat() | Hamster():
            return "small:" + p.speak()
        case _:
            return "?"


def main() -> None:
    print(describe(Dog("Rex")))
    print(describe(Cat()))
    print(describe(Hamster()))


main()
