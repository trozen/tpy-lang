from typing import Iterator
from tpy import Int32

class Cat:
    lives: Int32
    def __init__(self, lives: Int32) -> None:
        self.lives = lives

class Dog:
    lives: Int32
    def __init__(self, lives: Int32) -> None:
        self.lives = lives

class Holder:
    pet: Cat | Dog
    def __init__(self, pet: Cat | Dog) -> None:
        self.pet = pet

def gen(h: Holder) -> Iterator[Int32]:
    pet = Cat(1)
    yield pet.lives
    match h:
        case Holder(pet=Cat(lives=v)):
            yield v
            yield v + 1
        case _:
            yield 0
    yield pet.lives

def main() -> None:
    for v in gen(Holder(Cat(3))):
        print(v)

main()
