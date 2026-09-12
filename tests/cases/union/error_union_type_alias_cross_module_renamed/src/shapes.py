# Defines record types and a union type alias exported for cross-module use
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
