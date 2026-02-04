from tpy import Int32
from typing import Protocol

# Protocol defined first (required for CPython compatibility)
# Note: TurboPython also supports forward references where the protocol
# is defined after the class, but CPython doesn't allow this.
class Printable(Protocol):
    def __str__(self) -> str:
        ...


class Describable(Protocol):
    def describe(self) -> str:
        ...


# Class implementing protocols defined in same file
class Person(Printable, Describable):
    name: str
    age: Int32

    def __init__(self, name: str, age: Int32) -> None:
        self.name = name
        self.age = age

    def __str__(self) -> str:
        return self.name

    def describe(self) -> str:
        return "A person"


# Test that it works
p = Person("Alice", 30)
print(p.__str__())
print(p.describe())
print(p.name)
print(p.age)
