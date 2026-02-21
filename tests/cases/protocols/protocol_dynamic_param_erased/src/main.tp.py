# Passing an already-erased @dynamic protocol variable to a function
from tpy import dynamic
from typing import Protocol

@dynamic
class Pet(Protocol):
    def make_noise(self) -> str:
        ...

class Dog(Pet):
    def make_noise(self) -> str:
        return "Woof"

def greet(pet: Pet) -> None:
    print(pet.make_noise())

def main() -> None:
    pet: Pet = Dog()
    greet(pet)

main()
