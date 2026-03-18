# Readonly narrowed union param passed to mutable free function must be rejected
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
def print_dog(d: Dog) -> None:
    print(d.name)

@readonly
def bad(pet: Dog | Cat) -> None:
    if isinstance(pet, Dog):
        rename_dog(pet)  # tpyc: error(/Cannot pass readonly.*as mutable/)
        print_dog(pet)   # tpyc: ok

def main() -> None:
    pass

main()
