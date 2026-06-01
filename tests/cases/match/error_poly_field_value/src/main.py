# A *literal* field sub-pattern in a polymorphic-dispatch arm is supported
# (see poly_field_value); a *named-constant* (value) comparison is not, since
# only literal field checks are emitted. Sema points at the guard form.
import enum
from typing import Protocol
from tpy import dynamic


class Size(enum.Enum):
    SMALL = 1
    BIG = 2


@dynamic
class Pet(Protocol):
    def speak(self) -> str: ...


class Dog(Pet):
    size: Size

    def __init__(self, size: Size) -> None:
        self.size = size

    def speak(self) -> str:
        return "woof"


def describe(p: Pet) -> str:
    match p:
        case Dog(size=Size.BIG):  # tpyc: error(/comparing field 'size' against a named constant is not supported/)
            return "big"
        case _:
            return "?"


def main() -> None:
    print(describe(Dog(Size.BIG)))


main()
