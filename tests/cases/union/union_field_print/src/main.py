# operator<< for records with non-value union fields (visit-based printing)

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
    print(z)

    c = Cat("Whiskers")
    pet2: Dog | Cat = c
    z2 = Zoo(pet2, "cats")
    print(z2)

main()
