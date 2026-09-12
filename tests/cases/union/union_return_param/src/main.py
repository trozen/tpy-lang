# Return non-value union from param (zero-copy pointer variant pass-through)
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

def identity(pet: Dog | Cat) -> Dog | Cat:
    return pet

def get_name(pet: Dog | Cat) -> str:
    if isinstance(pet, Dog):
        return pet.name
    if isinstance(pet, Cat):
        return pet.name
    return ""

def main() -> None:
    d = Dog("Rex", 5)
    pet: Dog | Cat = d
    result = identity(pet)
    print(get_name(result))

    c = Cat("Whiskers", 9)
    pet2: Dog | Cat = c
    result2 = identity(pet2)
    print(get_name(result2))

main()
