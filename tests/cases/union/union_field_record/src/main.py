# Record with non-value union field: construct, read, narrow

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

def main() -> None:
    d = Dog("Rex")
    pet: Dog | Cat = d
    z = Zoo(pet, "test")
    print(z.tag)
    p = z.pet
    if isinstance(p, Dog):
        print(p.name)

    c = Cat("Whiskers")
    pet2: Dog | Cat = c
    z2 = Zoo(pet2, "cats")
    print(z2.tag)
    p2 = z2.pet
    if isinstance(p2, Cat):
        print(p2.name)

main()
