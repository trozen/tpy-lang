# `paint` carries an enum param, so a cross-module param-type lookup must
# resolve the enum type.
from enum import Enum


class Color(Enum):
    NONE = 0
    RED = 1


def paint(c: Color) -> bool:
    return c == Color.RED
