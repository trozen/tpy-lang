# Return narrowed member from isinstance branch (pointer variant)
from tpy import int32

class Dog:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name

class Cat:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name

def ensure_dog(pet: Dog | Cat) -> Dog | Cat:
    if isinstance(pet, Dog):
        return pet
    return pet

def pick_first_dog(a: Dog | Cat, b: Dog | Cat) -> Dog | Cat:
    if isinstance(a, Dog):
        return a
    if isinstance(b, Dog):
        return b
    return a

def main() -> None:
    d = Dog("Rex")
    c = Cat("Whiskers")
    pet: Dog | Cat = d

    result = ensure_dog(pet)
    if isinstance(result, Dog):
        print(result.name)

    pet2: Dog | Cat = c
    result2 = pick_first_dog(pet2, pet)
    if isinstance(result2, Dog):
        print(result2.name)

main()
