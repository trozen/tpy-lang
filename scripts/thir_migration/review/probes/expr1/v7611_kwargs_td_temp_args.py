from typing import TypedDict, Unpack
from tpy import Int32
class Point:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x
class TD(TypedDict):
    p: Point
    xs: list[Int32]
def g(**kw: Unpack[TD]) -> Int32:
    return kw["p"].x
def f(a: Point, b: Point, c: bool, ys: list[Int32]) -> Int32:
    return g(p=a if c else b, xs=[y + 1 for y in ys])
def main() -> None:
    pass
main()
