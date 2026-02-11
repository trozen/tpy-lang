from tpy import Int32


class Point:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x


x = None
print(x)
x = Point(Int32(7))
print(x.x)
