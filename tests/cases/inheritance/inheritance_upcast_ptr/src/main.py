# Pointer coercion: Dog -> Ptr[Animal], Ptr[Dog] -> Ptr[Animal], ConstPtr variants,
# multi-level (Puppy -> ConstPtr[Animal])
from tpy import Ptr, ConstPtr

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

def read_animal(p: ConstPtr[Animal]) -> None:
    print(p.name)

def main() -> None:
    d: Dog = Dog("Rex", "Lab")

    # Dog -> ConstPtr[Animal] (param passing)
    read_animal(d)

    # Ptr[Dog] -> Ptr[Animal]
    dp: Ptr[Dog] = Ptr(d)
    ap: Ptr[Animal] = dp
    print(ap.name)

    # Ptr[Dog] -> ConstPtr[Animal]
    cap: ConstPtr[Animal] = dp
    print(cap.name)

    # ConstPtr[Dog] -> ConstPtr[Animal]
    cdp: ConstPtr[Dog] = ConstPtr(d)
    cap2: ConstPtr[Animal] = cdp
    print(cap2.name)

    # Multi-level: Puppy -> ConstPtr[Animal] (grandchild -> grandparent)
    p: Puppy = Puppy("Tiny", "Corgi", 8)
    read_animal(p)

main()
