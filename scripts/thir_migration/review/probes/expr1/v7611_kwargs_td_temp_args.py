from typing import TypedDict, Unpack
from tpy import int32
class Point:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x
class TD(TypedDict):
    p: Point
    xs: list[int32]
def g(**kw: Unpack[TD]) -> int32:
    return kw["p"].x
def f(a: Point, b: Point, c: bool, ys: list[int32]) -> int32:
    return g(p=a if c else b, xs=[y + 1 for y in ys])
def main() -> None:
    pass
main()
