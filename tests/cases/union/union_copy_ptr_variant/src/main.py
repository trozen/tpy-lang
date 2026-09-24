# copy() on pointer-variant union local: variant-aware deep copy
import tpy
from tpy import copy

class Dog:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

class Cat:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

def print_copy_param(pet: Dog | Cat) -> None:
    pet2 = copy(pet)
    if isinstance(pet2, Dog):
        print(pet2.name)
    elif isinstance(pet2, Cat):
        print(pet2.name)

def copy_module_spelling(pet: Dog | Cat) -> None:
    # the module-qualified spelling at an annotated union local
    q: Dog | Cat = tpy.copy(pet)  # tpyc: ok
    if isinstance(pet, Dog):
        pet.name = "Moved"
    if isinstance(q, Dog):
        print("module", q.name)

def main() -> None:
    # Copy a Dog through a union-typed variable
    d = Dog("Rex")
    pet: Dog | Cat = d
    pet2 = copy(pet)
    d.name = "Changed"
    if isinstance(pet2, Dog):
        print(pet2.name)

    # Copy a Cat through a union-typed variable
    c = Cat("Whiskers")
    pet3: Dog | Cat = c
    pet4 = copy(pet3)
    if isinstance(pet4, Cat):
        print(pet4.name)

    # Copy through a function parameter
    d2 = Dog("Fido")
    param_pet: Dog | Cat = d2
    print_copy_param(param_pet)

    # Copy inside isinstance branch (narrowed context)
    c2 = Cat("Mittens")
    pet6: Dog | Cat = c2
    if isinstance(pet6, Cat):
        pet7 = copy(pet6)
        if isinstance(pet7, Cat):
            print(pet7.name)

    # Copy a nullable union
    pet8: Dog | Cat | None = Dog("Buddy")
    pet9 = copy(pet8)
    if pet9 is not None:
        if isinstance(pet9, Dog):
            print(pet9.name)

    # tpy.copy(...) takes the same copy as copy(...)
    copy_module_spelling(Dog("Spot"))

main()
