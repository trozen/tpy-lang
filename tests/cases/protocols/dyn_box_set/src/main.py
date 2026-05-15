# Box[P].set(value) for abstract @dynamic P -- replaces the current
# concrete conformer with a new one. Body lowers via tpy::heap_replace,
# whose abstract-T branch frees the old heap slot and takes ownership
# of the new one. Pins the post-construction mutation pattern.
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


class Dog(Pet):
    label: str
    def __init__(self, label: str) -> None:
        self.label = label
    def name(self) -> str:
        return self.label + "-woof"


def main() -> None:
    b: Box[Pet] = Box(Parrot(label="Polly"))
    print(b.get().name())
    # Replace inheritance-path conformer with a different inheritance-path conformer.
    b.set(Dog(label="Rex"))
    print(b.get().name())
    # Replace with another Parrot to exercise the slot-rewrite path twice.
    b.set(Parrot(label="Mimi"))
    print(b.get().name())


main()
