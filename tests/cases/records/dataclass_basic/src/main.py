# Basic @dataclass: auto-generated __init__ from field annotations
from dataclasses import dataclass
from tpy import int32

@dataclass
class Point:
    x: int32
    y: int32

def main() -> None:
    p = Point(1, 2)
    print(p)
    print(p.x, p.y)
    print(repr(p))
    p2 = Point(x=10, y=20)
    print(p2)

main()
