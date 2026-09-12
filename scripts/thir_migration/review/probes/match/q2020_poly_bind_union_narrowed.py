from typing import Protocol, Iterator, Optional
from tpy import dynamic, int32, Ptr

@dynamic
class Pet(Protocol):
    def speak(self) -> str: ...

class Person:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

class Dog(Pet):
    name: str
    owner: Person
    def __init__(self, name: str) -> None:
        self.name = name
        self.owner = Person("x")
    def speak(self) -> str:
        return "woof"

class Cat(Pet):
    name: str
    def __init__(self) -> None:
        self.name = "c"
    def speak(self) -> str:
        return "meow"

def f(p: Pet, u: Dog | Cat) -> str:
    if isinstance(u, Dog):
        match p:
            case Dog() as u:
                return u.name
            case _:
                return "o"
    return "n"

def main() -> None:
    print(f(Dog("r"), Cat()))

main()
