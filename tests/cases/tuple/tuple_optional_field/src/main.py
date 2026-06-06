# Test tuple[Optional[nonvalue], ...] as a record field
from typing import Optional
from tpy import Int32

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y
    def __repr__(self) -> str:
        return f"Point({self.x}, {self.y})"

class Holder:
    pair: tuple[Optional[Point], Int32]
    def __init__(self, pair: tuple[Optional[Point], Int32]) -> None:
        self.pair = pair  # tpyc: warning(/copies/)

def main() -> None:
    p = Point(1, 2)
    h1 = Holder((p, 42))
    print(h1.pair)

    h2 = Holder((None, 99))
    print(h2.pair)

main()
