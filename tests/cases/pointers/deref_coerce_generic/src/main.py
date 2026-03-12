from tpy import Int32, copy, readonly_alt

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

class Box[T]:
    _value: T
    def __init__(self, value: T) -> None:
        self._value = copy(value)
    @readonly_alt
    def __deref__(self) -> T:
        return self._value

def print_point(p: Point) -> None:
    print(p.x, p.y)

def test() -> None:
    b: Box[Point] = Box(Point(5, 15))
    # Field access through generic deref: Box[Point].__deref__() -> Point
    print(b.x)
    print(b.y)
    # Coercion: Box[Point] passed where Point expected
    print_point(b)

test()
