# @nocopy class as Box[Pet] conformer -- moves required, no copies allowed.
from typing import Protocol
from tpy import dynamic, nocopy
from tplib import Box

@dynamic
class Pet(Protocol):
    def name(self) -> str: ...

@nocopy
class Dog:
    label: str
    def __init__(self, label: str) -> None:
        self.label = label
    def name(self) -> str:
        return self.label

def main() -> None:
    d = Dog("Rex")
    b: Box[Pet] = Box(d)
    print(b.get().name())

main()
