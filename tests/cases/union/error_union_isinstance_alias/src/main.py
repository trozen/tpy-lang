# Error: isinstance with a type alias name is not supported
from tpy import int32


class Circle:
    radius: int32

    def __init__(self, radius: int32) -> None:
        self.radius = radius


class Rect:
    width: int32

    def __init__(self, width: int32) -> None:
        self.width = width


Shape = Circle | Rect


def check(s: Shape) -> None:
    if isinstance(s, Shape):  # tpyc: error(/does not accept the union alias 'Shape'/)
        print("yes")
