# Global (module-level) @dynamic protocol variable with reassignment and control flow
from tpy import dynamic, int32
from typing import Protocol

@dynamic
class Pet(Protocol):
    def name(self) -> str:
        ...

class Dog(Pet):
    def name(self) -> str:
        return "Rex"

class Cat:
    def name(self) -> str:
        return "Whiskers"

class Parrot:
    def name(self) -> str:
        return "Polly"

# Global init + reassign
pet: Pet = Dog()
print(pet.name())

pet = Cat()
print(pet.name())

# Global reassign inside branch
if True:
    pet = Parrot()
print(pet.name())

# Global reassign inside loop
i: int32 = 0
while i < 2:
    pet = Dog()
    i = i + 1
print(pet.name())
