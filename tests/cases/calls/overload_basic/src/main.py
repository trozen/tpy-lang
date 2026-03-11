# @overload dispatch with isinstance dead branch elimination
from typing import overload

class Dog:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

class Cat:
    lives: int
    def __init__(self, lives: int) -> None:
        self.lives = lives

@overload
def describe(animal: Dog) -> str: ...  # tpyc: ok

@overload
def describe(animal: Cat) -> str: ...  # tpyc: ok

def describe(animal: Dog | Cat) -> str:
    if isinstance(animal, Dog):
        return "Dog: " + animal.name
    else:
        return "Cat with " + str(animal.lives) + " lives"

def main() -> None:
    d = Dog("Rex")
    c = Cat(9)
    print(describe(d))
    print(describe(c))

main()
