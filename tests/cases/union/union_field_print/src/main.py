# Printing records with non-value union fields via __repr__ with isinstance dispatch
from dataclasses import dataclass

@dataclass
class Dog:
    name: str

@dataclass
class Cat:
    name: str

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
    # Print the union field components directly (narrowing required for union)
    p = z.pet
    if isinstance(p, Dog):
        print(f"Zoo(pet={repr(p)}, tag='{z.tag}')")
    else:
        print(f"Zoo(pet={repr(p)}, tag='{z.tag}')")

    c = Cat("Whiskers")
    pet2: Dog | Cat = c
    z2 = Zoo(pet2, "cats")
    p2 = z2.pet
    if isinstance(p2, Cat):
        print(f"Zoo(pet={repr(p2)}, tag='{z2.tag}')")
    else:
        print(f"Zoo(pet={repr(p2)}, tag='{z2.tag}')")

main()
