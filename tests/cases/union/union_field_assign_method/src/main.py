# Union field assignment via method: pointer-variant to value-variant in self.field = param

class Dog:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

class Cat:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

class Pen:
    pet: Dog | Cat
    def __init__(self, pet: Dog | Cat) -> None:
        self.pet = pet  # tpyc: warning(/copies/)

    def set_pet(self, pet: Dog | Cat) -> None:
        self.pet = pet  # tpyc: warning(/copies/)

def main() -> None:
    d = Dog("Rex")
    pet: Dog | Cat = d
    p = Pen(pet)

    # Reassign via method from pointer-variant param
    c = Cat("Whiskers")
    new_pet: Dog | Cat = c
    p.set_pet(new_pet)
    r = p.pet
    if isinstance(r, Cat):
        print(r.name)

    # Reassign via method again
    d2 = Dog("Buddy")
    buddy: Dog | Cat = d2
    p.set_pet(buddy)
    r2 = p.pet
    if isinstance(r2, Dog):
        print(r2.name)

main()
