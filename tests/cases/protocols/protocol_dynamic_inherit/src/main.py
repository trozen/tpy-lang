# Direct C++ inheritance from @dynamic protocol: Dog(Pet) -> struct Dog : __tpy_Pet_Base
# No adapter wrapping needed -- implicit upcast to Base& at call sites
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

def use_dog(d: Dog) -> None:
    greet(d)  # lvalue, direct inheritor -- no adapter, no copy

def main() -> None:
    greet(Dog())   # rvalue, direct inheritor -- materialized then upcast
    d = Dog()
    greet(d)       # lvalue, direct inheritor -- implicit upcast
    use_dog(d)
    pet: Pet = Dog()
    print(pet.make_noise())  # virtual dispatch via pointer-local

main()
