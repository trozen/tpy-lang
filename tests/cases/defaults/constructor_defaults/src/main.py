# Default parameter values in __init__ constructors
from tpy import Int32

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32 = Int32(0), y: Int32 = Int32(0)) -> None:
        self.x = x
        self.y = y

class Named:
    name: str
    value: Int32
    def __init__(self, name: str, value: Int32 = Int32(42)) -> None:
        self.name = name
        self.value = value

def main() -> None:
    p1 = Point()
    print(p1.x)
    print(p1.y)

    p2 = Point(Int32(3))
    print(p2.x)
    print(p2.y)

    p3 = Point(Int32(3), Int32(4))
    print(p3.x)
    print(p3.y)

    n1 = Named("test")
    print(n1.name)
    print(n1.value)

    n2 = Named("test", Int32(99))
    print(n2.name)
    print(n2.value)

main()
