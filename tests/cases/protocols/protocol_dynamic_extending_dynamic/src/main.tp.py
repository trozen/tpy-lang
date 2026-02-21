# @dynamic protocol extending @dynamic protocol -- base class inheritance chain.
# Tests transitive C++ upcast, erased child-to-parent, and structural conformance.
from tpy import dynamic
from typing import Protocol

@dynamic
class Pet(Protocol):
    def make_noise(self) -> str: ...

@dynamic
class NamedPet(Pet, Protocol):
    def name(self) -> str: ...

class Dog(NamedPet):
    def make_noise(self) -> str:
        return "Woof"
    def name(self) -> str:
        return "Rex"

class Parrot:
    def make_noise(self) -> str:
        return "Squawk"
    def name(self) -> str:
        return "Polly"

def greet_pet(pet: Pet) -> None:
    print(pet.make_noise())

def greet_named(pet: NamedPet) -> None:
    print(pet.name())

def main() -> None:
    dog = Dog()
    greet_pet(dog)           # Dog -> Base_NamedPet -> Base_Pet (transitive upcast)
    greet_named(dog)         # Dog -> Base_NamedPet (direct)
    np: NamedPet = Dog()
    greet_pet(np)            # NamedPet* -> Base_Pet& (child-to-parent base upcast)
    greet_named(Parrot())    # Structural -> Adapter_NamedPet -> Base_NamedPet
    parrot_np: NamedPet = Parrot()
    greet_pet(parrot_np)     # NamedPet* -> Base_Pet& (erased, upcast)

main()
