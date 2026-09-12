from typing import overload
from tpy import int32

class Dog:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

class Cat:
    lives: int32
    def __init__(self, lives: int32) -> None:
        self.lives = lives

@overload
def sniff(a: Dog) -> str: ...
@overload
def sniff(a: Cat) -> str: ...
def sniff(a: Dog | Cat) -> str:
    match a:
        case Cat(lives=9):
            return "nine"
        case _:
            return "other"

def main() -> None:
    print(sniff(Cat(9)))
    print(sniff(Dog("d")))

main()
