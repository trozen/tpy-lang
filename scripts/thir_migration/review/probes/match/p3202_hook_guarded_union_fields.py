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

def gen(a: Cat | Dog) -> Iterator[Int32]:
    match a:
        case Cat(lives=v) if v > 3:
            yield v
            yield v + 1
        case _:
            yield 0

def main() -> None:
    for v in gen(Cat(5)):
        print(v)

main()
