# Deref-view narrowing through an owning Box[Pet] wrapper: same mechanism as
# Rc, exercising Box's __deref__ path. isinstance(b, Dog) narrows the payload
# so b.bark() (a Dog-only method) resolves through the cast.
from typing import Protocol
from tpy import dynamic
from tplib.box import Box


@dynamic
class Pet(Protocol):
    def name(self) -> str: ...


class Dog(Pet):
    def name(self) -> str:
        return "dog"

    def bark(self) -> str:
        return "woof"


class Cat(Pet):
    def name(self) -> str:
        return "cat"


def describe(b: Box[Pet]) -> str:
    if isinstance(b, Dog):       # tpyc: ok
        return "dog:" + b.bark()
    return "other:" + b.name()


def main() -> None:
    print(describe(Box(Dog())))
    print(describe(Box(Cat())))


main()
