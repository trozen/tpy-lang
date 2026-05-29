# Deref-view narrowing through an owning Rc[Pet] wrapper. isinstance(rc, Dog)
# narrows the polymorphic payload reached via Rc's __deref__: rc.bark() (a Dog
# method, not on the Pet protocol) resolves against Dog through the cast, while
# rc.clone() stays an Rc method (the wrapper type is never narrowed). A
# structural conformer (Cat) routes through the adapter cast.
from typing import Protocol
from tpy import dynamic
from tplib.rc import Rc


@dynamic
class Pet(Protocol):
    def name(self) -> str: ...


class Dog(Pet):                  # inheritance conformer
    def name(self) -> str:
        return "dog"

    def bark(self) -> str:
        return "woof"


class Cat:                       # structural conformer, no inheritance
    label: str

    def __init__(self, label: str) -> None:
        self.label = label

    def name(self) -> str:
        return self.label


def describe(rc: Rc[Pet]) -> str:
    if isinstance(rc, Dog):      # tpyc: ok
        shared = rc.clone()      # clone() stays an Rc method (not narrowed)
        return "dog:" + rc.bark() + ":" + shared.name()
    if isinstance(rc, Cat):      # structural conformer through the deref view
        return "cat:" + rc.name()
    return "other:" + rc.name()


def main() -> None:
    print(describe(Rc.new(Dog())))
    print(describe(Rc.new(Cat("felix"))))


main()
