# @dynamic protocol with multiple methods, including void return
from tpy import dynamic
from typing import Protocol

@dynamic
class Shape(Protocol):
    def area(self) -> float:
        ...
    def name(self) -> str:
        ...
    def scale(self, factor: float) -> None:
        ...

class Circle(Shape):
    radius: float

    def __init__(self, radius: float) -> None:
        self.radius = radius

    def area(self) -> float:
        return 3.14159 * self.radius * self.radius

    def name(self) -> str:
        return "Circle"

    def scale(self, factor: float) -> None:
        self.radius = self.radius * factor

def main() -> None:
    c = Circle(5.0)
    print(c.name())
    print(c.area())
    c.scale(2.0)
    print(c.area())

main()
