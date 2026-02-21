# @dynamic protocol as local variable type with virtual dispatch
from tpy import dynamic
from typing import Protocol

@dynamic
class Pet(Protocol):
    def make_noise(self) -> str:
        ...

class Dog(Pet):
    def make_noise(self) -> str:
        return "Woof"

def main() -> None:
    pet: Pet = Dog()
    print(pet.make_noise())

main()
