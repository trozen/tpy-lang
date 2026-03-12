# Pointer coercion: Dog -> Ptr[Animal], Ptr[Dog] -> Ptr[Animal], Ptr[readonly[...]] variants,
# multi-level (Puppy -> Ptr[readonly[Animal]])
from tpy import Ptr, readonly

class Animal:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

class Dog(Animal):
    breed: str
    def __init__(self, name: str, breed: str) -> None:
        super().__init__(name)
        self.breed = breed

class Puppy(Dog):
    age_weeks: int
    def __init__(self, name: str, breed: str, age_weeks: int) -> None:
        super().__init__(name, breed)
        self.age_weeks = age_weeks

def read_animal(p: Ptr[readonly[Animal]]) -> None:
    print(p.name)

def main() -> None:
    d: Dog = Dog("Rex", "Lab")

    # Dog -> Ptr[readonly[Animal]] (param passing)
    read_animal(d)

    # Ptr[Dog] -> Ptr[Animal]
    dp: Ptr[Dog] = Ptr(d)
    ap: Ptr[Animal] = dp
    print(ap.name)

    # Ptr[Dog] -> Ptr[readonly[Animal]]
    cap: Ptr[readonly[Animal]] = dp
    print(cap.name)

    # Ptr[readonly[Dog]] -> Ptr[readonly[Animal]]
    cdp: Ptr[readonly[Dog]] = Ptr(d)
    cap2: Ptr[readonly[Animal]] = cdp
    print(cap2.name)

    # Multi-level: Puppy -> Ptr[readonly[Animal]] (grandchild -> grandparent)
    p: Puppy = Puppy("Tiny", "Corgi", 8)
    read_animal(p)

main()
