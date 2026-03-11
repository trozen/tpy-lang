# Error: @overload stub parameter names must match implementation
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
def describe(a: Dog) -> str: ...  # tpyc: error(/parameter/)

@overload
def describe(b: Cat) -> str: ...

def describe(animal: Dog | Cat) -> str:
    return "animal"
