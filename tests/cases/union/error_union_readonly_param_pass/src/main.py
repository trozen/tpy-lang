# A readonly-narrowed union member is a readonly Dog, so passing it to a
# mutable Dog parameter must be rejected. The @readonly callee that DOES
# accept it is pinned by tests/cases/union/union_field_const_read.
from tpy import readonly

class Dog:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

class Cat:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

def rename_dog(d: Dog) -> None:
    d.name = "Bad"

@readonly
def bad(pet: Dog | Cat) -> None:
    if isinstance(pet, Dog):
        rename_dog(pet)  # tpyc: error(/Cannot pass readonly.*as mutable/)

def main() -> None:
    pass

main()
