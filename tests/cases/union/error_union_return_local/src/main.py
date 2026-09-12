# Error: returning locally constructed union value (dangling pointer variant)
from tpy import int32

class Dog:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name

class Cat:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name

def make_pet() -> Dog | Cat:
    return Dog("Rex")  # tpyc: error(/Cannot return local or temporary/)
