# TypedDict passed to and returned from functions
from typing import TypedDict
from tpy import Int32, Own

class Point(TypedDict):
    x: Int32
    y: Int32

def describe(p: Point) -> str:
    return "(" + str(p["x"]) + ", " + str(p["y"]) + ")"

def translate(p: Point, dx: Int32, dy: Int32) -> Own[Point]:
    return Point(x=p["x"] + dx, y=p["y"] + dy)

def main() -> None:
    p = Point(x=Int32(1), y=Int32(2))
    print(describe(p))

    p2 = translate(p, Int32(10), Int32(20))
    print(describe(p2))

main()
