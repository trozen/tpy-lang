# @readonly method accessing union field: self.field is const in const method
from tpy import readonly

class Dog:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

class Cat:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

class Inner:
    pet: Dog | Cat
    def __init__(self, pet: Dog | Cat) -> None:
        self.pet = pet  # tpyc: warning(/copies/)

class Zoo:
    inner: Inner
    def __init__(self, inner: Inner) -> None:
        self.inner = inner

    @readonly
    def get_pet_name(self) -> str:
        p = self.inner.pet
        if isinstance(p, Dog):
            return p.name
        elif isinstance(p, Cat):
            return p.name
        return ""

    def get_pet_name_auto(self) -> str:
        p = self.inner.pet
        if isinstance(p, Dog):
            return p.name
        elif isinstance(p, Cat):
            return p.name
        return ""

    def rename_pet(self, new_name: str) -> None:
        # Mutate through pointer-variant local -- proves reference semantics
        p = self.inner.pet
        if isinstance(p, Dog):
            p.name = new_name

def main() -> None:
    d = Dog("Rex")
    pet: Dog | Cat = d
    z = Zoo(Inner(pet))
    print(z.get_pet_name())
    print(z.get_pet_name_auto())

    c = Cat("Whiskers")
    pet2: Dog | Cat = c
    z2 = Zoo(Inner(pet2))
    print(z2.get_pet_name())
    print(z2.get_pet_name_auto())

    # Prove reference semantics: mutate through union local, check via field
    z.rename_pet("Buddy")
    print(z.get_pet_name())

main()
