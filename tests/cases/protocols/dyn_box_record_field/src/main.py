# Box[P] for @dynamic P as a record field -- the storage form of an
# erased dynamic-protocol value lives behind Box[P] (heap-owned).
from typing import Protocol
from tpy import dynamic, Own
from tplib import Box


@dynamic
class Pet(Protocol):
    def name(self) -> str: ...


class Parrot(Pet):
    label: str
    def __init__(self, label: str) -> None:
        self.label = label
    def name(self) -> str:
        return self.label


class Dog:
    label: str
    def __init__(self, label: str) -> None:
        self.label = label
    def name(self) -> str:
        return self.label


class Owner:
    name_: str
    pet: Box[Pet]
    def __init__(self, name_: str, pet: Own[Box[Pet]]) -> None:
        self.name_ = name_
        self.pet = pet


def main() -> None:
    alice = Owner("Alice", Box(Parrot(label="Polly")))
    bob = Owner("Bob", Box(Dog(label="Rex")))
    print(alice.name_, alice.pet.get().name())
    print(bob.name_, bob.pet.get().name())


main()
