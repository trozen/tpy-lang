# Default parameter values in __init__ constructors
from tpy import int32

class Point:
    x: int32
    y: int32
    def __init__(self, x: int32 = int32(0), y: int32 = int32(0)) -> None:
        self.x = x
        self.y = y

class Named:
    name: str
    value: int32
    def __init__(self, name: str, value: int32 = int32(42)) -> None:
        self.name = name
        self.value = value

def main() -> None:
    p1 = Point()
    print(p1.x)
    print(p1.y)

    p2 = Point(int32(3))
    print(p2.x)
    print(p2.y)

    p3 = Point(int32(3), int32(4))
    print(p3.x)
    print(p3.y)

    n1 = Named("test")
    print(n1.name)
    print(n1.value)

    n2 = Named("test", int32(99))
    print(n2.name)
    print(n2.value)

main()
