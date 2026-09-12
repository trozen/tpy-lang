# match/case on concrete record with guards and or-patterns
from dataclasses import dataclass
from tpy import int32

@dataclass
class Point:
    x: int32
    y: int32

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
    print(guarded(Point(int32(0), int32(0))))
    print(guarded(Point(int32(5), int32(3))))
    print(guarded(Point(int32(-1), int32(0))))
    print(or_pattern(Point(int32(0), int32(0))))
    print(or_pattern(Point(int32(1), int32(1))))
    print(or_pattern(Point(int32(2), int32(3))))

main()
