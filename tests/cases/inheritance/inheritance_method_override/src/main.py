from tpy import int32

class Shape:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name

    def area(self) -> int32:
        return 0

    def describe(self) -> str:
        return self.name


class Square(Shape):
    side: int32

    def __init__(self, side: int32) -> None:
        self.name = "Square"
        self.side = side

    def area(self) -> int32:
        return self.side * self.side


class Rectangle(Shape):
    width: int32
    height: int32

    def __init__(self, width: int32, height: int32) -> None:
        self.name = "Rectangle"
        self.width = width
        self.height = height

    def area(self) -> int32:
        return self.width * self.height


# Test method override
s = Square(5)
print(s.describe())
print(s.area())

r = Rectangle(4, 6)
print(r.describe())
print(r.area())

# Test parent class still works
base = Shape("Base")
print(base.describe())
print(base.area())
