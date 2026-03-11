# @overload on methods with isinstance dead branch elimination
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
    count: int
    def __init__(self) -> None:
        self.count = 0

    @overload
    def treat(self, animal: Dog) -> str: ...

    @overload
    def treat(self, animal: Cat) -> str: ...

    def treat(self, animal: Dog | Cat) -> str:
        self.count = self.count + 1
        if isinstance(animal, Dog):
            return "Treated dog: " + animal.name
        else:
            return "Treated cat with " + str(animal.lives) + " lives"

def main() -> None:
    v = Vet()
    d = Dog("Rex")
    c = Cat(7)
    print(v.treat(d))
    print(v.treat(c))
    print(v.count)

main()
