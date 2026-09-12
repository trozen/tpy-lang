# @dataclass with user-defined __eq__ (user wins, no synthesis)
from dataclasses import dataclass
from typing import Self
from tpy import int32

@dataclass
class Point:
    x: int32
    y: int32
    def __eq__(self, other: Self) -> bool:  # tpyc: ok
        return self.x == other.x

def main() -> None:
    p1 = Point(1, 2)
    p2 = Point(1, 3)
    print(p1 == p2)

main()
