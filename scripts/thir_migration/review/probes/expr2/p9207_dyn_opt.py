from typing import Protocol
from tpy import dynamic, Int32
@dynamic
class Pet(Protocol):
    def n(self) -> Int32: ...
class Dog(Pet):
    def n(self) -> Int32:
        return 1
def f(p: Pet | None) -> Int32:
    return p.n()
def main() -> None:
    d = Dog()
    print(f(d))
main()
