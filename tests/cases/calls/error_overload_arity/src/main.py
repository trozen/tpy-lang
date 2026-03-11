# Error: @overload stub has different number of params than implementation
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
def describe(animal: Dog, verbose: bool) -> str: ...  # tpyc: error(/parameter/)

@overload
def describe(animal: Cat) -> str: ...

def describe(animal: Dog | Cat) -> str:
    return "animal"
