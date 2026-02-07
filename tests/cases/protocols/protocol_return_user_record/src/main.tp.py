from typing import Protocol
from tpy import Int32, Own

class Point:
    x: Int32
    y: Int32

    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

class PointFactory(Protocol):
    def create_point(self, x: Int32, y: Int32) -> Own[Point]: ...

class DefaultFactory:
    def create_point(self, x: Int32, y: Int32) -> Own[Point]:
        return Point(x, y)

def make_point[T: PointFactory](factory: T, x: Int32, y: Int32) -> Own[Point]:
    return factory.create_point(x, y)

def main() -> None:
    factory = DefaultFactory()
    p = make_point(factory, 10, 20)
    print(p.x)
    print(p.y)

main()
