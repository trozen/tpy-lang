from tpy import Int32

class Shape:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name

    def area(self) -> Int32:
        return 0

    def describe(self) -> str:
        return self.name


class Square(Shape):
    side: Int32

    def __init__(self, side: Int32) -> None:
        self.name = "Square"
        self.side = side

    def area(self) -> Int32:
        return self.side * self.side


class Rectangle(Shape):
    width: Int32
    height: Int32

    def __init__(self, width: Int32, height: Int32) -> None:
        self.name = "Rectangle"
        self.width = width
        self.height = height

    def area(self) -> Int32:
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
