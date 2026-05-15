# list[Box[P]] for @dynamic P -- heterogeneous container of conformers.
# Mixes one inheritance-path conformer (Parrot) with one structural-conformance
# conformer (Dog). Virtual dispatch through Pet routes each name() call to
# the right concrete impl.
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


class Dog:
    label: str
    def __init__(self, label: str) -> None:
        self.label = label
    def name(self) -> str:
        return self.label


def main() -> None:
    pets: list[Box[Pet]] = []
    pets.append(Box(Parrot(label="Polly")))
    pets.append(Box(Dog(label="Rex")))
    for p in pets:
        print(p.get().name())


main()
