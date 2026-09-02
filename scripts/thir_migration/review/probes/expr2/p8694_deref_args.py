from typing import Protocol
from tpy import dynamic, Int32
from tplib.box import Box
@dynamic
class Pet(Protocol):
    def name(self) -> str: ...
class Dog(Pet):
    def name(self) -> str:
        return "dog"
    def bark(self) -> str:
        return "woof"
    def bark_at(self, k: Int32) -> str:
        return "woof"
def a1(b: Box[Pet]) -> str:
    if isinstance(b, Dog):
        return b.bark_at(2)
    return "x"
def a2(b: Box[Pet]) -> str:
    if isinstance(b, Dog):
        return b.bark()
    return "x"
def main() -> None:
    print(a1(Box(Dog())), a2(Box(Dog())))
main()
