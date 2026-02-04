from tpy import Int32
from typing import Protocol

# Define a custom protocol
class Printable(Protocol):
    def __str__(self) -> str:
        ...


# Class that explicitly implements Printable
class Person(Printable):
    name: str
    age: Int32

    def __init__(self, name: str, age: Int32) -> None:
        self.name = name
        self.age = age

    def __str__(self) -> str:
        return self.name


# Test explicit protocol implementation
p = Person("Alice", 30)
print(p.__str__())
print(p.name)
print(p.age)
