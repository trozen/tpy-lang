from tpy import int32


class Point:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


x = None
print(x)
x = Point(int32(7))
print(x.x)
