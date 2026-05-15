# Error: passing a non-conformer to Own[P] where P is @dynamic.
# Cat is missing the name() method that Pet requires.
from typing import Protocol
from tpy import dynamic
from tplib import Box


@dynamic
class Pet(Protocol):
    def name(self) -> str: ...


class Cat:
    label: str
    def __init__(self, label: str) -> None:
        self.label = label
    # No name() method -- doesn't conform to Pet.


def main() -> None:
    box: Box[Pet] = Box(Cat(label="Whiskers"))  # tpyc: error(/Pet|conform/)


main()
