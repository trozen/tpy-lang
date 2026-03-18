# Mutation through narrowed readonly union field in @readonly method must be rejected
from tpy import readonly

class Dog:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

class Cat:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

class Zoo:
    pet: Dog | Cat
    def __init__(self, pet: Dog | Cat) -> None:
        self.pet = pet

    @readonly
    def try_mutate(self) -> None:
        p = self.pet
        if isinstance(p, Dog):
            p.name = "Bad"  # tpyc: error(/Cannot mutate readonly/)

def main() -> None:
    pass

main()
