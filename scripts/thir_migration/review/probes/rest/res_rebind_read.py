from tpy import Int32
class Dog:
    def sound(self) -> str:
        return "woof"
class Cat:
    def sound(self) -> str:
        return "meow"
async def step(n: Int32) -> Int32:
    return n + 1
from typing import Iterator
def echo(a: Dog | Cat) -> Dog | Cat:
    return a
def vals() -> Iterator[str]:
    a: Dog | Cat = Dog()
    if isinstance(a, Dog):
        yield a.sound()
        a = echo(a)
        yield "end"
def main() -> None:
    pass
main()
