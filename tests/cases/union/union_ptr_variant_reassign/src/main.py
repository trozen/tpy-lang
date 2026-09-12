# Pointer-variant union reassignment with storage slots
from tpy import int32

class Dog:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name

class Cat:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name

def main() -> None:
    # Init from concrete rvalue, then reassign to different type
    pet: Dog | Cat = Dog("Rex")
    if isinstance(pet, Dog):
        print(pet.name)

    pet = Cat("Whiskers")
    if isinstance(pet, Cat):
        print(pet.name)

    # Reassign back to Dog
    pet = Dog("Buddy")
    if isinstance(pet, Dog):
        print(pet.name)

    # Reassign from another ptr-variant local
    other: Dog | Cat = Cat("Luna")
    pet = other
    if isinstance(pet, Cat):
        print(pet.name)

main()
