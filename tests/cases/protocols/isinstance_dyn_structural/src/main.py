# isinstance(p, Cat) where Cat STRUCTURALLY conforms to @dynamic Pet (does not
# inherit it). Behind the Pet* borrow a structural Cat is an Adapter<Pet,Cat>
# (rvalue source) or RefAdapter<Pet,Cat> (lvalue source); codegen narrows via
# tpy::dyn_adapter_cast through the adapter's `.inner` instead of dynamic_cast.
from typing import Protocol
from tpy import dynamic


@dynamic
class Pet(Protocol):
    def name(self) -> str: ...


class Dog(Pet):                  # inheritance conformer
    def name(self) -> str:
        return "dog"


class Cat:                       # structural conformer, no inheritance
    label: str

    def __init__(self, label: str) -> None:
        self.label = label

    def name(self) -> str:
        return self.label

    def purr(self) -> str:
        return self.label + "-purr"


def describe(p: Pet) -> str:
    if isinstance(p, Cat):       # tpyc: ok
        return "cat:" + p.purr()  # purr is Cat-only -- needs the adapter narrowing
    return "other:" + p.name()


def main() -> None:
    print(describe(Dog()))           # inheritance conformer -> not a Cat
    print(describe(Cat("felix")))    # structural rvalue -> Adapter
    whiskers = Cat("whiskers")
    print(describe(whiskers))        # structural lvalue -> RefAdapter


main()
