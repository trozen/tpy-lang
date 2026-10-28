# A class pattern naming what a handle holds, on the handle itself, can never
# match (CPython tests the Box), so it is the impossible-pattern error, with a
# hint to match the object the handle holds.
from typing import Protocol
from tpy import dynamic
from tplib.box import Box


@dynamic
class Pet(Protocol):
    def name(self) -> str: ...


class Dog(Pet):
    def __init__(self) -> None:
        pass

    def name(self) -> str:
        return "dog"


def describe(b: Box[Pet]) -> str:
    match b:
        case Dog():  # tpyc: error(/class pattern 'Dog' does not match subject type 'Box'; to match the object it holds, use match deref\(b\)/)
            return "dog"
        case _:
            return "other"


def main() -> None:
    print(describe(Box(Dog())))


main()
