# TypedDict passed to and returned from functions
from typing import TypedDict
from tpy import int32, Own

class Point(TypedDict):
    x: int32
    y: int32

def describe(p: Point) -> str:
    return "(" + str(p["x"]) + ", " + str(p["y"]) + ")"

def translate(p: Point, dx: int32, dy: int32) -> Own[Point]:
    return Point(x=p["x"] + dx, y=p["y"] + dy)

def main() -> None:
    p = Point(x=int32(1), y=int32(2))
    print(describe(p))

    p2 = translate(p, int32(10), int32(20))
    print(describe(p2))

main()
