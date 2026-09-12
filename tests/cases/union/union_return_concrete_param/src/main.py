# Return concrete-typed param as union (Dog -> Dog | Cat via pointer variant)
from tpy import int32

class Dog:
    name: str
    age: int32

    def __init__(self, name: str, age: int32) -> None:
        self.name = name
        self.age = age

class Cat:
    name: str
    lives: int32

    def __init__(self, name: str, lives: int32) -> None:
        self.name = name
        self.lives = lives

def wrap_dog(d: Dog) -> Dog | Cat:
    return d

def wrap_cat(c: Cat) -> Dog | Cat:
    return c

def main() -> None:
    d = Dog("Rex", 5)
    result = wrap_dog(d)
    if isinstance(result, Dog):
        print(result.name, result.age)

    c = Cat("Whiskers", 9)
    result2 = wrap_cat(c)
    if isinstance(result2, Cat):
        print(result2.name, result2.lives)

main()
