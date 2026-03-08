# match/case on concrete record with guards and or-patterns
from dataclasses import dataclass
from tpy import Int32

@dataclass
class Point:
    x: Int32
    y: Int32

def guarded(p: Point) -> str:
    match p:
        case Point(x=0, y=0):
            return "origin"
        case Point(x=x) if x > 0:
            return "positive x"
        case _:
            return "other"
    return ""

def or_pattern(p: Point) -> str:
    match p:
        case Point(x=0, y=0) | Point(x=1, y=1):
            return "special"
        case _:
            return "other"
    return ""

def main() -> None:
    print(guarded(Point(Int32(0), Int32(0))))
    print(guarded(Point(Int32(5), Int32(3))))
    print(guarded(Point(Int32(-1), Int32(0))))
    print(or_pattern(Point(Int32(0), Int32(0))))
    print(or_pattern(Point(Int32(1), Int32(1))))
    print(or_pattern(Point(Int32(2), Int32(3))))

main()
