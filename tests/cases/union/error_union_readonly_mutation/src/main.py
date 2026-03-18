# Mutation through narrowed readonly union param must be rejected
from tpy import readonly

class Dog:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

class Cat:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

@readonly
def try_mutate(pet: Dog | Cat) -> None:
    if isinstance(pet, Dog):
        pet.name = "Bad"  # tpyc: error(/Cannot mutate readonly/)

def main() -> None:
    pass

main()
