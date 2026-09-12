from typing import Protocol
from tpy import int32, dynamic
@dynamic
class Pet(Protocol):
    def name(self) -> str: ...
class Dog(Pet):
    def name(self) -> str:
        return "dog"
    def bark(self) -> str:
        return "woof"
class Cat(Pet):
    def name(self) -> str:
        return "cat"
    def __str__(self) -> str:
        return 'cat'
def describe(p: Pet) -> None:
    assert isinstance(p, Dog)
    print(p.bark())
    print(p)
def main() -> None:
    describe(Dog())
main()
