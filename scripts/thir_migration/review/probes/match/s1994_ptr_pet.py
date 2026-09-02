from typing import Protocol, Iterator, Optional
from tpy import dynamic, Int32, Ptr

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

def f(p: Ptr[Pet]) -> str:
    match p:
        case Dog():
            return "d"
        case _:
            return "o"

def call(np: Pet) -> str:
    return f(np)

def main() -> None:
    print(call(Dog("r")))

main()
