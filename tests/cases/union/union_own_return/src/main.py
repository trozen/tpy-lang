# Own[Dog | Cat] returns value variant by value (no pointer variant)
from tpy import Int32, Own

class Dog:
    name: str
    age: Int32

    def __init__(self, name: str, age: Int32) -> None:
        self.name = name
        self.age = age

class Cat:
    name: str
    lives: Int32

    def __init__(self, name: str, lives: Int32) -> None:
        self.name = name
        self.lives = lives

def make_dog(name: str, age: Int32) -> Own[Dog | Cat]:
    return Dog(name, age)

def make_cat(name: str, lives: Int32) -> Own[Dog | Cat]:
    return Cat(name, lives)

def main() -> None:
    pet = make_dog("Rex", 5)
    if isinstance(pet, Dog):
        print(pet.name, pet.age)

    pet2 = make_cat("Whiskers", 9)
    if isinstance(pet2, Cat):
        print(pet2.name, pet2.lives)

main()
