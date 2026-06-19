# Error: isinstance with a type alias name is not supported
from tpy import Int32


class Circle:
    radius: Int32

    def __init__(self, radius: Int32) -> None:
        self.radius = radius


class Rect:
    width: Int32

    def __init__(self, width: Int32) -> None:
        self.width = width


Shape = Circle | Rect


def check(s: Shape) -> None:
    if isinstance(s, Shape):  # tpyc: error(/does not accept the union alias 'Shape'/)
        print("yes")
