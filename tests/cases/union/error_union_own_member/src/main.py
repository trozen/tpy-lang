# Own[T] cannot appear as a union member -- use Own[A | B] instead
from tpy import Own

class Dog:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

class Cat:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

def bad(c: Cat) -> Cat | Own[Dog]:  # tpyc: error(/Own\[Dog\] cannot be a union member/)
    return c
