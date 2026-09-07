# An inline poly isinstance under `and`: the left operand's dynamic cast proves
# the narrowing, so the right operand reads the derived method directly.
from typing import Protocol
from tpy import Int32, dynamic


@dynamic
class Tagged(Protocol):
    pass


class Pet(Tagged):
    def __init__(self) -> None:
        pass

    def name(self) -> str:
        return "pet"


class Dog(Pet):
    def __init__(self) -> None:
        super().__init__()

    def bark(self) -> str:
        return "woof"


def check_and(p: Pet, threshold: Int32) -> bool:
    return isinstance(p, Dog) and len(p.bark()) > threshold


def main() -> None:
    print(check_and(Dog(), 2), check_and(Dog(), 9), check_and(Pet(), 0))


main()
