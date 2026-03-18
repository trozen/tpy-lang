# Readonly narrowed union param passed as method arg to mutable param must be rejected
from tpy import readonly

class Dog:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

class Cat:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

class Kennel:
    def __init__(self) -> None:
        pass

    def adopt(self, d: Dog) -> None:
        d.name = "Adopted"

@readonly
def bad(pet: Dog | Cat, k: Kennel) -> None:
    if isinstance(pet, Dog):
        k.adopt(pet)  # tpyc: error(/Cannot pass readonly.*as mutable/)

def main() -> None:
    pass

main()
