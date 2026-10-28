# isinstance on the object an Rc[Pet] holds, bound by name (`pet = rc.get()`):
# pet.bark() (a Dog method, not on the Pet protocol) resolves against Dog
# through the cast, while rc.clone() stays an Rc method. A structural
# conformer (Cat) routes through the adapter cast.
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

    @property
    def loud(self) -> str:
        return "WOOF"


class Cat:                       # structural conformer, no inheritance
    label: str

    def __init__(self, label: str) -> None:
        self.label = label

    def name(self) -> str:
        return self.label


def describe(rc: Rc[Pet]) -> str:
    pet = rc.get()
    if isinstance(pet, Dog):     # tpyc: ok
        shared = rc.clone()      # clone() stays an Rc method
        # A Dog property reads through the same cast as the method.
        return "dog:" + pet.bark() + ":" + pet.loud + ":" + shared.name()  # tpyc: ok
    if isinstance(pet, Cat):     # structural conformer
        return "cat:" + pet.name()
    return "other:" + pet.name()


def main() -> None:
    print(describe(Rc.new(Dog())))
    print(describe(Rc.new(Cat("felix"))))


main()
