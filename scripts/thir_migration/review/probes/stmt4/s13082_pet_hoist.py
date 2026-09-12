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
    def meow(self) -> str:
        return "meow"
def f(p: Pet) -> str:
    if isinstance(p, Dog):
        s = p.bark()
    else:
        s = p.name()
    return s
def main() -> None:
    print(f(Dog()))
main()
