# Box[P] for @dynamic protocol P via explicit inheritance.
# Heap-owns a Parrot through Box<Pet>, dispatches name() virtually.
from typing import Protocol
from tpy import dynamic
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


def main() -> None:
    box: Box[Pet] = Box(Parrot(label="Polly"))
    print(box.get().name())


main()
