# Passing a child with only added fields/methods (no overrides) as a parent-typed
# arg does NOT warn -- static dispatch on Parent's methods is semantically correct
# because Child didn't override anything.
from tpy import Int32

class Animal:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name
    def describe(self) -> str:
        return self.name

class Dog(Animal):
    breed: str
    # Adds a field and a new method but does NOT override describe()
    def __init__(self, name: str, breed: str) -> None:
        super().__init__(name)
        self.breed = breed
    def bark(self) -> str:  # new method, not an override
        return "Woof!"

def name_of(a: Animal) -> str:
    return a.describe()

def main() -> None:
    d: Dog = Dog("Rex", "Lab")
    print(name_of(d))  # tpyc: ok
    print(d.bark())

main()
