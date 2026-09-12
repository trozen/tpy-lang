from typing import Iterator
from tpy import int32

class Cat:
    lives: int32
    def __init__(self, lives: int32) -> None:
        self.lives = lives

class Dog:
    lives: int32
    def __init__(self, lives: int32) -> None:
        self.lives = lives

class Holder:
    pet: Cat | Dog
    def __init__(self, pet: Cat | Dog) -> None:
        self.pet = pet

def gen(h: Holder) -> Iterator[int32]:
    match h:
        case Holder(pet=Cat(lives=v)):
            yield v
            yield v + 1
        case _:
            yield 0

def main() -> None:
    for v in gen(Holder(Cat(3))):
        print(v)

main()
