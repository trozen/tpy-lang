from tpy import dynamic, readonly, int32, Own
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
from typing import Iterator
def gen(c: bool) -> Iterator[str]:
    if c:
        p: Pet = Dog()
    else:
        p = Cat()
    for i in range(2):
        yield p.name()
def main() -> None:
    for v in gen(True):
        print(v)
main()
