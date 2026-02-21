# Cross-module @dynamic extending @dynamic: child protocol imports and extends parent from another module.
# Tests that __tpy_Base_NamedPet inherits from the qualified ::tpy_user::pet::__tpy_Base_Pet.
from pet import Pet
from tpy import dynamic
from typing import Protocol

@dynamic
class NamedPet(Pet, Protocol):
    def name(self) -> str: ...

class Dog(NamedPet):
    def speak(self) -> str:
        return "Woof"
    def name(self) -> str:
        return "Rex"

class Parrot:
    def speak(self) -> str:
        return "Squawk"
    def name(self) -> str:
        return "Polly"

def greet_pet(pet: Pet) -> None:
    print(pet.speak())

def greet_named(pet: NamedPet) -> None:
    print(pet.name())

def main() -> None:
    dog = Dog()
    greet_pet(dog)           # Dog -> Base_NamedPet -> Base_Pet (transitive cross-module upcast)
    greet_named(dog)         # Dog -> Base_NamedPet (direct)
    np: NamedPet = Dog()
    greet_pet(np)            # NamedPet* -> Base_Pet& (child-to-parent, cross-module)
    greet_named(Parrot())    # Structural -> Adapter_NamedPet
    parrot_np: NamedPet = Parrot()
    greet_pet(parrot_np)     # NamedPet* -> Base_Pet& (erased, cross-module upcast)

main()
