# @overload stubs with different return types per variant
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
def get_value(animal: Dog) -> str: ...  # tpyc: ok

@overload
def get_value(animal: Cat) -> int: ...  # tpyc: ok

def get_value(animal: Dog | Cat) -> str | int:
    if isinstance(animal, Dog):
        return animal.name
    else:
        return animal.lives

def use_dog_result(name: str) -> None:
    print("Dog name: " + name)

def use_cat_result(lives: int) -> None:
    print("Cat lives: " + str(lives))

def main() -> None:
    d = Dog("Rex")
    c = Cat(9)

    dog_val = get_value(d)  # tpyc: type(str)
    cat_val = get_value(c)  # tpyc: type(int)

    use_dog_result(dog_val)
    use_cat_result(cat_val)

main()
