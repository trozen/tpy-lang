# Error: Child -> Own[Parent] would silently slice the object, so it's rejected.
# Use the concrete type or a @dynamic protocol for polymorphism.
from tpy import Own

class Animal:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

class Dog(Animal):
    breed: str
    def __init__(self, name: str, breed: str) -> None:
        super().__init__(name)
        self.breed = breed

def take(x: Own[Animal]) -> None:
    print(x.name)

def main() -> None:
    take(Dog("Rex", "Lab"))  # tpyc: error(/expected Animal, got Dog/)

main()
