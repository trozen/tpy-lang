# Union field assignment: pointer-variant to value-variant conversion

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
        self.pet = pet  # tpyc: warning(/copies/)
        self.tag = tag

def identity(pet: Dog | Cat) -> Dog | Cat:
    return pet

def main() -> None:
    d = Dog("Rex")
    init_pet: Dog | Cat = d
    z = Zoo(init_pet, "v1")

    # Reassign field from a pointer-variant local
    new_pet: Dog | Cat = Cat("Whiskers")
    z.pet = new_pet  # tpyc: warning(/copies/)
    p = z.pet
    if isinstance(p, Cat):
        print(p.name)

    # Reassign field from a function returning pointer-variant
    z.pet = identity(new_pet)  # tpyc: warning(/copies.*into field/) warning(/Mutation.*while borrowed/)
    p2 = z.pet
    if isinstance(p2, Cat):
        print(p2.name)

    # Reassign field from a constructor (already value -- no conversion needed)
    z.pet = Dog("Buddy")  # tpyc: warning(/Mutation.*while borrowed/)
    p3 = z.pet
    if isinstance(p3, Dog):
        print(p3.name)

    print(z.tag)

main()
