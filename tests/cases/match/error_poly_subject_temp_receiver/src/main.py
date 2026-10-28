# A polymorphic match on the object a TEMPORARY handle holds
# (`match make_box(kind).get():`) is refused: no `auto&` can hold a borrow
# of the temporary, and a by-value bind would slice it so no subclass arm
# matches (BUGS.md#poly-match-temp-receiver-subject).
from typing import Protocol
from tpy import dynamic, Own
from tplib import Box


@dynamic
class Tag(Protocol):
    pass


class Animal(Tag):
    legs: int

    def __init__(self, legs: int) -> None:
        self.legs = legs


class Dog(Animal):
    def __init__(self) -> None:
        super().__init__(4)


def make_box() -> Own[Box[Animal]]:
    return Box(Dog())


def describe() -> str:
    match make_box().get():  # tpyc: error(/match\.poly_subject_copy/)
        case Dog():
            return "dog"
        case _:
            return "?"


def main() -> None:
    print(describe())


main()
