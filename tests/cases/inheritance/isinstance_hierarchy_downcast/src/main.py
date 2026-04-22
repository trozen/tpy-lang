# Downcast isinstance folds to False and emits a warning -- the static
# type is authoritative and slicing at the boundary has removed the
# Child fields.
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

class Cat(Animal):
    whiskers: int
    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.whiskers = 6


def value_downcast(a: Animal) -> bool:
    return isinstance(a, Dog)  # tpyc: warning(/descendant type 'Dog'/)

def ptr_downcast(a: Ptr[Animal]) -> bool:
    return isinstance(a, Dog)  # tpyc: warning(/descendant type 'Dog'/)


def multi_downcast_tuple(a: Animal) -> bool:
    # Tuple form warns once per offending descendant.
    return isinstance(a, (Dog, Cat))  # tpyc: warning(/descendant type 'Dog'/) warning(/descendant type 'Cat'/)


def main() -> None:
    # ptr_downcast is compiled for snapshot+warning verification but not
    # called here: passing a real Dog to `Ptr[Animal]` would return True
    # under CPython's runtime dispatch and False under tpyc's static fold.
    print(value_downcast(Animal("Mystery")))
    print(multi_downcast_tuple(Animal("Generic")))


main()
