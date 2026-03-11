# @overload stub returns wrong type for its branch
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
def get_value(animal: Dog) -> str: ...

@overload
def get_value(animal: Cat) -> int: ...

def get_value(animal: Dog | Cat) -> str | int:
    if isinstance(animal, Dog):
        return 42  # tpyc: error(/return type mismatch.*int.*str/)
    else:
        return animal.lives
