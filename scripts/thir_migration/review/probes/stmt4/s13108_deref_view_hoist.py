from typing import Protocol
from tpy import Int32, dynamic
from tplib.box import Box
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
def describe(b: Box[Pet]) -> str:
    if isinstance(b, Dog):
        s = b.bark()
    else:
        s = b.name()
    return s
def main() -> None:
    print(describe(Box(Dog())))
main()
