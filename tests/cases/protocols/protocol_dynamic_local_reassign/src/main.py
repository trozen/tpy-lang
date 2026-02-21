# @dynamic protocol local reassigned to different concrete type
from tpy import dynamic
from typing import Protocol

@dynamic
class Pet(Protocol):
    def make_noise(self) -> str:
        ...

class Dog(Pet):
    def make_noise(self) -> str:
        return "Woof"

class Cat(Pet):
    def make_noise(self) -> str:
        return "Meow"

def main() -> None:
    pet: Pet = Dog()
    print(pet.make_noise())
    pet = Cat()
    print(pet.make_noise())

main()
