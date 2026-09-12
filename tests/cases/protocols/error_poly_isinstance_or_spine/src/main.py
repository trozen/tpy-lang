# An inline poly isinstance under `or`: the `and` spine installs the narrowing
# fact for its right operand, but `or` installs nothing, so the read after it
# has no proven cast to render.
from typing import Protocol
from tpy import int32, dynamic


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


def check_or(p: Pet, threshold: int32) -> bool:
    return isinstance(p, Dog) or len(p.name()) > threshold  # tpyc: error(/expr\.call/)


def main() -> None:
    print(check_or(Dog(), 2))


main()
