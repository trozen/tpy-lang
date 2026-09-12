from typing import Protocol
from tpy import int32, Own

class Point:
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

class PointFactory(Protocol):
    def create_point(self, x: int32, y: int32) -> Own[Point]: ...

class DefaultFactory:
    def create_point(self, x: int32, y: int32) -> Own[Point]:
        return Point(x, y)

def make_point[T: PointFactory](factory: T, x: int32, y: int32) -> Own[Point]:
    return factory.create_point(x, y)

def main() -> None:
    factory = DefaultFactory()
    p = make_point(factory, 10, 20)
    print(p.x)
    print(p.y)

main()
