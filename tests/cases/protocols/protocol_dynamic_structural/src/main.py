# Structural conformance with @dynamic protocol (no explicit inheritance)
# Tests owning adapter (rvalue) and ref adapter (lvalue) paths
from tpy import dynamic
from typing import Protocol

@dynamic
class Pet(Protocol):
    def make_noise(self) -> str:
        ...

# Parrot satisfies Pet structurally but does NOT inherit it
class Parrot:
    def make_noise(self) -> str:
        return "Squawk"

def greet(pet: Pet) -> None:
    print(pet.make_noise())

def main() -> None:
    greet(Parrot())       # rvalue: owning adapter
    p = Parrot()
    greet(p)              # lvalue: ref adapter (zero-copy)
    pet: Pet = Parrot()   # local: owning adapter (owns inner value)
    print(pet.make_noise())

main()
