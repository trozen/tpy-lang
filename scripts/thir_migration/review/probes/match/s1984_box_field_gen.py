from typing import Protocol, Iterator
from tpy import dynamic, Own
from tplib import Box

@dynamic
class Tag(Protocol):
    pass

class Animal(Tag):
    legs: int
    def __init__(self, legs: int) -> None:
        self.legs = legs

class Dog(Animal):
    def __init__(self) -> None:
        super().__init__(4)

class Owner:
    pet: Box[Animal]
    def __init__(self, pet: Own[Box[Animal]]) -> None:
        self.pet = pet

def gen(o: Owner) -> Iterator[str]:
    match o.pet:
        case Dog():
            print("dog")
        case _:
            print("?")
    yield "x"

def main() -> None:
    for s in gen(Owner(Box(Dog()))):
        print(s)

main()
