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


@readonly
def show_dog(d: Dog) -> str:
    return d.name


# Free function: the readonly-narrowed union member is itself a readonly Dog,
# so it admits only at a @readonly parameter -- the mutable sibling is rejected
# by tests/cases/union/error_union_readonly_param_pass.
@readonly
def forward_pet(pet: Dog | Cat) -> str:
    if isinstance(pet, Dog):
        return show_dog(pet)  # tpyc: ok
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

    # Renaming through the record proves the narrowed member reaching the
    # readonly callee still aliases `d`, rather than a copy taken at the
    # union local.
    print("forward_pet:", forward_pet(pet))
    d.name = "Buddy"
    print("forward_pet:", forward_pet(pet))
    print("forward_pet:", forward_pet(pet2))

main()
