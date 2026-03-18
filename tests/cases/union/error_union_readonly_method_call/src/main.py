# Non-readonly method call through narrowed readonly union param must be rejected
from tpy import readonly

class Dog:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

    def rename(self, new_name: str) -> None:
        self.name = new_name

class Cat:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

@readonly
def bad(pet: Dog | Cat) -> None:
    if isinstance(pet, Dog):
        pet.rename("Bad")  # tpyc: error(/Cannot call non-readonly method.*readonly/)

def main() -> None:
    pass

main()
