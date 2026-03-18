# Read non-value union field from non-mutated param (const context)
from tpy import readonly

class Dog:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

class Cat:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

class Zoo:
    pet: Dog | Cat
    tag: str
    def __init__(self, pet: Dog | Cat, tag: str) -> None:
        self.pet = pet
        self.tag = tag

def get_pet_name(z: Zoo) -> str:
    p = z.pet
    if isinstance(p, Dog):
        return p.name
    if isinstance(p, Cat):
        return p.name
    return ""

@readonly
def get_pet_name_ro(z: Zoo) -> str:
    p = z.pet
    if isinstance(p, Dog):
        return p.name
    if isinstance(p, Cat):
        return p.name
    return ""

@readonly
def greet_pet(pet: Dog | Cat) -> str:
    if isinstance(pet, Dog):
        return pet.name
    elif isinstance(pet, Cat):
        return pet.name
    return ""

def main() -> None:
    d = Dog("Rex")
    pet: Dog | Cat = d
    z = Zoo(pet, "test")
    print(get_pet_name(z))

    c = Cat("Whiskers")
    pet2: Dog | Cat = c
    z2 = Zoo(pet2, "cats")
    print(get_pet_name(z2))

    # @readonly function accessing union field on record
    print(get_pet_name_ro(z))

    # @readonly function with direct union param
    print(greet_pet(pet))

main()
