# A deref-view isinstance over an Optional Box PARAM: the narrowing cast is
# rendered for rebind-slot pointer locals, and this wrapper variable is not one.
from typing import Optional, Protocol
from tpy import dynamic
from tplib.box import Box


@dynamic
class Pet(Protocol):
    def name(self) -> str: ...


class Dog(Pet):
    def name(self) -> str:
        return "dog"


class Cat(Pet):
    def name(self) -> str:
        return "cat"


def h(ob: Optional[Box[Pet]]) -> str:
    if ob is None:
        return "none"
    if isinstance(ob, Dog):  # tpyc: error(/cond\.call/)
        return ob.name()
    return "other"


def main() -> None:
    print(h(None), h(Box(Cat())))


main()
