# match/case on Optional[Record] (pointer repr) and Optional[primitive]
from typing import Optional
from dataclasses import dataclass
from tpy import int32
from enum import Enum, auto

@dataclass
class Point:
    x: int32
    y: int32

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
    print(check_point(Point(int32(0), int32(0))))
    print(check_point(Point(int32(3), int32(4))))
    print(check_color(None))
    print(check_color(Color.Red))
    print(check_color(Color.Green))

main()
