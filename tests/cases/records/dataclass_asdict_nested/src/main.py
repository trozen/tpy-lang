# Test asdict() with nested dataclass fields
from dataclasses import dataclass, asdict, astuple
from tpy import int32

@dataclass
class Point:
    x: int32
    y: int32

@dataclass
class Line:
    start: Point
    end: Point

def main() -> None:
    line = Line(Point(int32(1), int32(2)), Point(int32(3), int32(4)))
    d = asdict(line)
    print(d)
    t = astuple(line)
    print(t)

main()
