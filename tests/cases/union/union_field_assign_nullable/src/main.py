# Nullable union field assignment: pointer-variant with None (monostate)

class Dog:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

class Cat:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

class Shelter:
    pet: Dog | Cat | None
    def __init__(self, pet: Dog | Cat | None) -> None:
        self.pet = pet  # tpyc: warning(/copies/)

def main() -> None:
    d = Dog("Rex")
    init_pet: Dog | Cat | None = d
    s = Shelter(init_pet)

    # Assign from pointer-variant local
    new_pet: Dog | Cat | None = Cat("Whiskers")
    s.pet = new_pet
    p = s.pet
    if p is not None:
        if isinstance(p, Cat):
            print(p.name)

    # Assign again from pointer-variant local
    another: Dog | Cat | None = Dog("Buddy")
    s.pet = another  # tpyc: warning(/Mutation.*while borrowed/)
    p2 = s.pet
    if p2 is not None:
        if isinstance(p2, Dog):
            print(p2.name)

main()
