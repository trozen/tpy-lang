from tpy import int32
from typing import Protocol

# Define custom protocols
class Printable(Protocol):
    def __str__(self) -> str:
        ...


class Describable(Protocol):
    def describe(self) -> str:
        ...


class Measurable(Protocol):
    def size(self) -> int32:
        ...


# Class that implements multiple protocols
class Box(Printable, Describable, Measurable):
    width: int32
    height: int32

    def __init__(self, width: int32, height: int32) -> None:
        self.width = width
        self.height = height

    def __str__(self) -> str:
        return "Box"

    def describe(self) -> str:
        return "A rectangular box"

    def size(self) -> int32:
        return self.width * self.height


# Test multiple protocol implementation
b = Box(5, 3)
print(b.__str__())
print(b.describe())
print(b.size())
print(b.width)
print(b.height)
