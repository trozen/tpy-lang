from tpy import dynamic, readonly, Int32, Own
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
        return "Tom"
def mk() -> Own[Pet]:
    return Dog()
def main() -> None:
    p: Pet = mk()
    print(p.name())
main()
