# The guarded polymorphic-match tier: an or-arm ANDs its guard into the block
# condition, and a guarded wildcard gates its body: `case Cat() | Hamster()
# if flag` and `case _ if not flag` in `size_of`, printing "small",
# "unflagged", and "?".
from typing import Protocol
from tpy import dynamic


@dynamic
class Pet(Protocol):
    def speak(self) -> str: ...


class Dog(Pet):
    tag: str

    def __init__(self, tag: str) -> None:
        self.tag = tag

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


def size_of(p: Pet, flag: bool) -> str:
    match p:
        case Cat() | Hamster() if flag:  # guarded or-arm
            return "small"
        case _ if not flag:  # guarded wildcard
            return "unflagged"
        case _:
            return "?"


def main() -> None:
    print(size_of(Cat(), True))
    print(size_of(Dog("d"), False))
    print(size_of(Dog("d"), True))


main()
