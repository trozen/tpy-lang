# Ptr[T] is folded the same as T: the static pointee type is authoritative.
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


def via_ptr_up(d: Ptr[Dog]) -> bool:
    return isinstance(d, Animal)

def via_ptr_same(d: Ptr[Dog]) -> bool:
    return isinstance(d, Dog)


def main() -> None:
    d = Dog("Rex", "lab")
    print(via_ptr_up(d))
    print(via_ptr_same(d))


main()
