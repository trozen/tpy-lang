from typing import Protocol
from tpy import dynamic, int32
@dynamic
class Pet(Protocol):
    def n(self) -> int32: ...
class Dog(Pet):
    def n(self) -> int32:
        return 1
def f(p: Pet | None) -> int32:
    return p.n()
def main() -> None:
    d = Dog()
    print(f(d))
main()
