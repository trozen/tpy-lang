# Dynamic protocol var-to-var assignment (erased to erased pointer copy)
from tpy import dynamic
from typing import Protocol

@dynamic
class Pet(Protocol):
    def name(self) -> str: ...

class Dog(Pet):
    def name(self) -> str:
        return "Rex"

class Cat:
    def name(self) -> str:
        return "Whiskers"

def greet(pet: Pet) -> None:
    print(pet.name())

def main() -> None:
    p1: Pet = Dog()
    p2: Pet = p1
    greet(p1)
    greet(p2)
    # Reassign to concrete, then back to erased
    p2 = Cat()
    greet(p2)
    p2 = p1
    greet(p2)

main()
