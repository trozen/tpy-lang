# Combined: @dynamic protocol locals, params, reassignment, multiple protocols
from tpy import dynamic
from typing import Protocol

@dynamic
class Describable(Protocol):
    def describe(self) -> str:
        ...

@dynamic
class Noise(Protocol):
    def make_noise(self) -> str:
        ...

class Dog(Describable, Noise):
    def describe(self) -> str:
        return "a dog"
    def make_noise(self) -> str:
        return "Woof"

class Cat(Describable, Noise):
    def describe(self) -> str:
        return "a cat"
    def make_noise(self) -> str:
        return "Meow"

def show_desc(d: Describable) -> None:
    print(d.describe())

def show_noise(n: Noise) -> None:
    print(n.make_noise())

def main() -> None:
    d: Describable = Dog()
    show_desc(d)
    d = Cat()
    show_desc(d)

    n: Noise = Cat()
    show_noise(n)
    n = Dog()
    show_noise(n)

    show_desc(Dog())
    show_noise(Cat())

main()
