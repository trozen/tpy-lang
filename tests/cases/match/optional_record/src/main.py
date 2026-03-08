# match/case on Optional[Record] (pointer repr) and Optional[primitive]
from typing import Optional
from dataclasses import dataclass
from tpy import Int32
from enum import Enum, auto

@dataclass
class Point:
    x: Int32
    y: Int32

class Color(Enum):
    Red = auto()
    Green = auto()

def check_point(p: Optional[Point]) -> str:
    match p:
        case None:
            return "none"
        case Point(x=0, y=0):
            return "origin"
        case Point(x=x, y=y):
            return str(x) + "," + str(y)
    return ""

def check_color(c: Optional[Color]) -> str:
    match c:
        case None:
            return "none"
        case Color.Red:
            return "red"
        case _:
            return "other"
    return ""

def main() -> None:
    print(check_point(None))
    print(check_point(Point(Int32(0), Int32(0))))
    print(check_point(Point(Int32(3), Int32(4))))
    print(check_color(None))
    print(check_color(Color.Red))
    print(check_color(Color.Green))

main()
