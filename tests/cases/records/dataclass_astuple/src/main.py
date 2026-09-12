# Test astuple() on a flat dataclass
from dataclasses import dataclass, astuple
from tpy import int32

@dataclass
class Point:
    x: int32
    y: int32

def main() -> None:
    p = Point(int32(1), int32(2))
    t = astuple(p)
    print(t)

main()
