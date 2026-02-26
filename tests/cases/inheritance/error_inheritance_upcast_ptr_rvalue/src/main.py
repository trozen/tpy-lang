# Error: cannot take pointer to rvalue (temporary) via inheritance upcast
from tpy import Ptr

class Animal:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

class Dog(Animal):
    breed: str
    def __init__(self, name: str, breed: str) -> None:
        super().__init__(name)
        self.breed = breed

def take_ptr(p: Ptr[Animal]) -> None:
    print(p.name)

def main() -> None:
    take_ptr(Dog("Rex", "Lab"))  # tpyc: error(/Cannot take mutable pointer/)

main()
