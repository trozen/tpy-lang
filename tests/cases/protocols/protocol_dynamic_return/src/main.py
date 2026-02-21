# Returning @dynamic protocol values from functions (param passthrough, globals)
from tpy import dynamic
from typing import Protocol

@dynamic
class Pet(Protocol):
    def name(self) -> str:
        ...

class Dog(Pet):
    def name(self) -> str:
        return "Rex"

class Cat(Pet):
    def name(self) -> str:
        return "Whiskers"

global_pet: Pet = Cat()

def echo(pet: Pet) -> Pet:
    return pet

def get_global() -> Pet:
    return global_pet

def main() -> None:
    # Return parameter
    dog = Dog()
    result: Pet = echo(dog)
    print(result.name())

    # Return global
    g: Pet = get_global()
    print(g.name())

    # Chain: return value used in another call
    print(echo(dog).name())

main()
