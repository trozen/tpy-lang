# @overload stub includes a type not present in the implementation's union
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
def describe(animal: Bird) -> str: ...  # tpyc: error(/not in the implementation/)

def describe(animal: Dog | Cat) -> str:
    if isinstance(animal, Dog):
        return "Dog: " + animal.name
    else:
        return "Cat with " + str(animal.lives) + " lives"
