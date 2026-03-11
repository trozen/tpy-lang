# Overloaded function in a separate module
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
def describe(animal: Dog) -> str: ...

@overload
def describe(animal: Cat) -> str: ...

def describe(animal: Dog | Cat) -> str:
    if isinstance(animal, Dog):
        return "Dog: " + animal.name
    else:
        return "Cat: " + str(animal.lives)
