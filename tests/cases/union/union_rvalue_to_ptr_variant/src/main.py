# Constructor rvalue passed to pointer-variant union param
# Tests rvalue materialization for function, constructor, and method calls

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

def greet(pet: Dog | Cat) -> None:
    if isinstance(pet, Dog):
        print(pet.name)
    elif isinstance(pet, Cat):
        print(pet.name)

def main() -> None:
    # Rvalue to free function param
    greet(Dog("Rex"))
    greet(Cat("Whiskers"))

    # Rvalue to constructor param
    p = Pen(Dog("Buddy"))
    r = p.pet
    if isinstance(r, Dog):
        print(r.name)

    # Rvalue to method param
    p.set_pet(Cat("Mittens"))
    r2 = p.pet
    if isinstance(r2, Cat):
        print(r2.name)

main()
