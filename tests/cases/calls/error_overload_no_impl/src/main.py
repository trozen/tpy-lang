# Error: @overload stubs with no implementation function
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
def describe(animal: Dog) -> str: ...  # tpyc: error(/no implementation/)

@overload
def describe(animal: Cat) -> str: ...
