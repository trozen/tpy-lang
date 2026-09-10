# Calling an overloaded function with a type that matches no stub
from typing import overload

class Dog:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

class Cat:
    lives: int
    def __init__(self, lives: int) -> None:
        self.lives = lives

class Bird:
    can_fly: bool
    def __init__(self, can_fly: bool) -> None:
        self.can_fly = can_fly

@overload
def describe(animal: Dog) -> str: ...

@overload
def describe(animal: Cat) -> str: ...

def describe(animal: Dog | Cat) -> str:
    if isinstance(animal, Dog):
        return "Dog: " + animal.name
    else:
        return "Cat with " + str(animal.lives) + " lives"

def main() -> None:
    b = Bird(True)
    print(describe(b))  # tpyc: error(/No matching overload/)

main()
