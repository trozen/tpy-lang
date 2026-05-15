# Multi-hop @dynamic inheritance: NamedPet extends Pet (both @dynamic).
# Cat inherits NamedPet (which inherits Pet). Box[Pet] = Box(Cat()) should
# pick the Covariant uplift path, not the structural wrapping path,
# because Cat transitively inherits Pet.
from typing import Protocol
from tpy import dynamic
from tplib import Box

@dynamic
class Pet(Protocol):
    def name(self) -> str: ...

@dynamic
class NamedPet(Pet, Protocol):
    def label(self) -> str: ...

class Cat(NamedPet):
    n: str
    def __init__(self, n: str) -> None:
        self.n = n
    def name(self) -> str: return self.n
    def label(self) -> str: return self.n

def main() -> None:
    b: Box[Pet] = Box(Cat("Whiskers"))
    print(b.get().name())

main()
