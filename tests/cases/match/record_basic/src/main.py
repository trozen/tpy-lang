# match/case on concrete record subjects with field-value matching
from dataclasses import dataclass
from tpy import int32

@dataclass
class Point:
    x: int32
    y: int32

def describe(p: Point) -> str:
    match p:
        case Point(x=0, y=0):
            return "origin"
        case Point(x=x, y=0):
            return "x-axis: " + str(x)
        case Point(x=0, y=y):
            return "y-axis: " + str(y)
        case Point(x=x, y=y):
            return "(" + str(x) + ", " + str(y) + ")"
    return ""

def check_quadrant(p: Point) -> str:
    match p:
        case Point(x=0, y=0):
            return "origin"
        case _:
            return "not origin"
    return ""

def positional(p: Point) -> str:
    match p:
        case Point(0, 0):
            return "origin"
        case Point(_, 0):
            return "x-axis"
        case _:
            return "other"
    return ""

def with_capture(p: Point) -> str:
    match p:
        case Point() as q:
            return "point: " + str(q.x) + ", " + str(q.y)
    return ""

def main() -> None:
    print(describe(Point(int32(0), int32(0))))
    print(describe(Point(int32(3), int32(0))))
    print(describe(Point(int32(0), int32(5))))
    print(describe(Point(int32(3), int32(4))))
    print(check_quadrant(Point(int32(0), int32(0))))
    print(check_quadrant(Point(int32(1), int32(2))))
    print(positional(Point(int32(0), int32(0))))
    print(positional(Point(int32(5), int32(0))))
    print(positional(Point(int32(1), int32(2))))
    print(with_capture(Point(int32(7), int32(8))))

main()
