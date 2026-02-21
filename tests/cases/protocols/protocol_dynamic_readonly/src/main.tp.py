# @readonly methods on @dynamic protocol
from tpy import dynamic, readonly
from typing import Protocol

@dynamic
class Pet(Protocol):
    @readonly
    def name(self) -> str: ...

class Dog(Pet):
    @readonly
    def name(self) -> str:
        return "Rex"

class Cat:
    @readonly
    def name(self) -> str:
        return "Whiskers"

def greet(pet: Pet) -> None:
    print(pet.name())

def main() -> None:
    pet: Pet = Dog()
    print(pet.name())
    pet = Cat()
    print(pet.name())
    greet(Dog())
    greet(Cat())

main()
