# Defines a ValueType record for cross-module import.
from tpy import int32, ValueType


class Vec2(ValueType):
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y
