# Test asdict() with nested dataclass fields
from dataclasses import dataclass, asdict, astuple
from tpy import Int32

@dataclass
class Point:
    x: Int32
    y: Int32

@dataclass
class Line:
    start: Point
    end: Point

def main() -> None:
    line = Line(Point(Int32(1), Int32(2)), Point(Int32(3), Int32(4)))
    d = asdict(line)
    print(d)
    t = astuple(line)
    print(t)

main()
