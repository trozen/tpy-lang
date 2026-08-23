# An enum used in the three container positions that had no corpus coverage
# at all: a dict KEY, a set ELEMENT, and a value-tuple ELEMENT.
from enum import Enum
from tpy import Int64


class Color(Enum):
    Red = 0
    Green = 1
    Blue = 2


def weight(m: dict[Color, Int64], c: Color) -> Int64:
    return m[c]                      # enum as a dict key at a subscript read


def dedup(cs: list[Color]) -> Int64:
    out: set[Color] = set()
    for c in cs:
        out.add(c)                   # enum as a set element
    return Int64(len(out))


def tag_value(t: tuple[Color, Int64]) -> Int64:
    return t[1] if t[0] == Color.Green else 0  # enum as a tuple element


def main() -> None:
    m = {Color.Red: Int64(10), Color.Blue: Int64(30)}  # enum keys in a literal
    print(weight(m, Color.Blue))
    print(dedup([Color.Red, Color.Green, Color.Red]))
    print(tag_value((Color.Green, 7)))
    print(tag_value((Color.Red, 7)))


main()
