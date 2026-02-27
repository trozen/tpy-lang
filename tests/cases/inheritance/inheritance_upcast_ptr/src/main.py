# Pointer coercion: Dog -> Ptr[Animal], Ptr[Dog] -> Ptr[Animal], ReadOnlyPtr variants,
# multi-level (Puppy -> ReadOnlyPtr[Animal])
from tpy import Ptr, ReadOnlyPtr

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

def read_animal(p: ReadOnlyPtr[Animal]) -> None:
    print(p.name)

def main() -> None:
    d: Dog = Dog("Rex", "Lab")

    # Dog -> ReadOnlyPtr[Animal] (param passing)
    read_animal(d)

    # Ptr[Dog] -> Ptr[Animal]
    dp: Ptr[Dog] = Ptr(d)
    ap: Ptr[Animal] = dp
    print(ap.name)

    # Ptr[Dog] -> ReadOnlyPtr[Animal]
    cap: ReadOnlyPtr[Animal] = dp
    print(cap.name)

    # ReadOnlyPtr[Dog] -> ReadOnlyPtr[Animal]
    cdp: ReadOnlyPtr[Dog] = ReadOnlyPtr(d)
    cap2: ReadOnlyPtr[Animal] = cdp
    print(cap2.name)

    # Multi-level: Puppy -> ReadOnlyPtr[Animal] (grandchild -> grandparent)
    p: Puppy = Puppy("Tiny", "Corgi", 8)
    read_animal(p)

main()
