# Implicit value upcast: assign child to parent-typed variable, pass child as parent param
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

def greet(a: Animal) -> None:
    print(a.name)

def main() -> None:
    d: Dog = Dog("Rex", "Lab")
    # Direct upcast
    a: Animal = Dog("Buddy", "Poodle")
    print(a.name)
    # Param passing (const ref binding, no slicing)
    greet(d)
    greet(Dog("Max", "Beagle"))
    # Multi-level upcast (grandchild -> grandparent)
    p: Puppy = Puppy("Tiny", "Corgi", 8)
    greet(p)
    a2: Animal = p
    print(a2.name)

main()
