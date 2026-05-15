# Box[P] for @dynamic P via structural conformance.
# Dog has matching methods but doesn't inherit Pet; codegen wraps the
# Dog in a tpy::Adapter<Pet, Dog> when constructing Box[Pet](Dog(...)).
from typing import Protocol
from tpy import dynamic
from tplib import Box


@dynamic
class Pet(Protocol):
    def name(self) -> str: ...


class Dog:
    label: str
    def __init__(self, label: str) -> None:
        self.label = label
    def name(self) -> str:
        return self.label


def main() -> None:
    box: Box[Pet] = Box(Dog(label="Rex"))
    print(box.get().name())


main()
