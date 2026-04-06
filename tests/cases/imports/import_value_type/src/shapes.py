# Defines a ValueType record for cross-module import.
from tpy import Int32, ValueType


class Vec2(ValueType):
    x: Int32
    y: Int32

    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y
