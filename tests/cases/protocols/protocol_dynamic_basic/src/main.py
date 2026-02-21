# Basic @dynamic protocol: concept + base class + adapter are generated
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
    d = Dog()
    c = Cat()
    print(d.make_noise())
    print(c.make_noise())

main()
