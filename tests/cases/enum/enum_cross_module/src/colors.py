# Defines an enum type for cross-module import
from enum import Enum
from tpy import int32


class Color(Enum):
    Red = 0
    Green = 1
    Blue = 2


def color_value(c: Color) -> int32:
    return c.value
