# Defines record types and a union type alias
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
