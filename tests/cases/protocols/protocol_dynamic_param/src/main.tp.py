# @dynamic protocol as function parameter with call-site adapter wrapping
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

def greet(pet: Pet) -> None:
    print(pet.make_noise())

def main() -> None:
    greet(Dog())
    greet(Cat())

main()
