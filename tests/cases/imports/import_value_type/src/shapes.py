# Defines a ValueType record for cross-module import.
from tpy import Int32, ValueType


class Vec2(ValueType):
    x: Int32
    y: Int32
