from tpy import int32
from typing import Protocol

# Base class
class Entity:
    name: str
    id: int32

    def __init__(self, name: str, id: int32) -> None:
        self.name = name
        self.id = id

    def get_name(self) -> str:
        return self.name


# Protocol
class Printable(Protocol):
    def __str__(self) -> str:
        ...


# Inherit from class AND implement protocol
class Person(Entity, Printable):
    age: int32

    def __init__(self, name: str, id: int32, age: int32) -> None:
        self.name = name
        self.id = id
        self.age = age

    def __str__(self) -> str:
        return self.name


# Test combined inheritance
p = Person("Alice", 42, 30)

# Access inherited fields
print(p.name)
print(p.id)

# Access own field
print(p.age)

# Call inherited method
print(p.get_name())

# Call protocol method
print(p.__str__())
