# @overload method stub returns wrong type for its branch
from typing import overload

class Dog:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

class Cat:
    lives: int
    def __init__(self, lives: int) -> None:
        self.lives = lives

class Vet:
    @overload
    def treat(self, animal: Dog) -> str: ...

    @overload
    def treat(self, animal: Cat) -> int: ...

    def treat(self, animal: Dog | Cat) -> str | int:
        if isinstance(animal, Dog):
            return 99  # tpyc: error(/return type mismatch.*int.*str/)
        else:
            return animal.lives
