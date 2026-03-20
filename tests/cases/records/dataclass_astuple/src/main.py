# Test astuple() on a flat dataclass
from dataclasses import dataclass, astuple
from tpy import Int32

@dataclass
class Point:
    x: Int32
    y: Int32

def main() -> None:
    p = Point(Int32(1), Int32(2))
    t = astuple(p)
    print(t)

main()
