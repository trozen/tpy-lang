# Test asdict()/astuple() with Optional[Dataclass] fields and nested containers
from dataclasses import dataclass, asdict, astuple
from typing import Optional
from tpy import Int32

@dataclass
class Point:
    x: Int32
    y: Int32

# Direct Optional[DC] field
@dataclass
class MaybePoint:
    label: str
    pos: Optional[Point]

# list[Optional[DC]] field
@dataclass
class PointList:
    items: list[Optional[Point]]

# Nested: Optional[DC] where DC itself has a nested DC
@dataclass
class Line:
    start: Point
    end: Point

@dataclass
class MaybeLine:
    line: Optional[Line]

# Mixed: Optional + non-Optional DC fields
@dataclass
class Mixed:
    name: str
    required: Point
    optional: Optional[Point]

# dict[str, Optional[DC]]
@dataclass
class LabeledPoints:
    items: dict[str, Optional[Point]]

def main() -> None:
    # 1. Optional[DC] present
    mp1 = MaybePoint("origin", Point(0, 0))
    print(asdict(mp1))
    print(astuple(mp1))

    # 2. Optional[DC] absent
    mp2 = MaybePoint("none", None)
    print(asdict(mp2))
    print(astuple(mp2))

    # 3. list[Optional[DC]]
    pl = PointList([Point(1, 2), None, Point(3, 4)])
    print(asdict(pl))
    print(astuple(pl))

    # 4. Deeply nested Optional
    ml1 = MaybeLine(Line(Point(0, 0), Point(1, 1)))
    print(asdict(ml1))
    print(astuple(ml1))

    ml2 = MaybeLine(None)
    print(asdict(ml2))
    print(astuple(ml2))

    # 5. Mixed required + optional
    m1 = Mixed("both", Point(1, 2), Point(3, 4))
    print(asdict(m1))

    m2 = Mixed("req-only", Point(5, 6), None)
    print(asdict(m2))

    # 6. dict[str, Optional[DC]]
    lp = LabeledPoints({"a": Point(1, 2), "b": None, "c": Point(3, 4)})
    print(asdict(lp))

main()
