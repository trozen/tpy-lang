# @dataclass with user-defined __init__ (user wins, no synthesis)
from dataclasses import dataclass
from tpy import int32

@dataclass
class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32) -> None:  # tpyc: ok
        self.x = x * 2
        self.y = y

def main() -> None:
    p = Point(1, 2)
    print(p.x, p.y)

main()
