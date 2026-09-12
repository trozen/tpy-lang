# Fuzz-style coverage of asdict()/astuple() across nested, list, dict, and tuple field types
from dataclasses import dataclass, asdict, astuple
from tpy import int32

# Simple flat
@dataclass
class Point:
    x: int32
    y: int32

# Mixed types
@dataclass
class Person:
    name: str
    age: int32

# Nested dataclass
@dataclass
class Line:
    start: Point
    end: Point

# Nested with mixed types
@dataclass
class NamedPoint:
    name: str
    pos: Point

# List of dataclasses
@dataclass
class Polygon:
    vertices: list[Point]

# Nested list of dataclasses with mixed types
@dataclass
class Drawing:
    title: str
    shapes: list[Point]

# Deeply nested
@dataclass
class Wrapper:
    inner: NamedPoint

# Dataclass with optional field
@dataclass
class MaybeNamed:
    name: str
    value: int32 = int32(0)

# Empty list field
@dataclass
class Container:
    items: list[Point]

# Multiple list fields
@dataclass
class MultiList:
    points: list[Point]
    labels: list[str]

# Dict with dataclass values
@dataclass
class DictOfDC:
    items: dict[str, Point]

# Tuple with dataclass element
@dataclass
class TupleMixed:
    pair: tuple[Point, int32]

# Tuple with all dataclass elements
@dataclass
class TupleAllDC:
    pair: tuple[Point, Point]

def main() -> None:
    # 1. Flat homogeneous
    p = Point(int32(1), int32(2))
    print(asdict(p))
    print(astuple(p))

    # 2. Mixed types
    person = Person("Alice", int32(30))
    print(asdict(person))
    print(astuple(person))

    # 3. Nested dataclass (homogeneous)
    line = Line(Point(int32(0), int32(0)), Point(int32(1), int32(1)))
    print(asdict(line))
    print(astuple(line))

    # 4. Nested with mixed types
    np = NamedPoint("origin", Point(int32(0), int32(0)))
    print(asdict(np))

    # 5. List of dataclasses
    poly = Polygon([Point(int32(0), int32(0)), Point(int32(1), int32(0)), Point(int32(0), int32(1))])
    print(asdict(poly))

    # 6. Mixed types + list
    d = Drawing("sketch", [Point(int32(1), int32(2))])
    print(asdict(d))

    # 7. Deeply nested
    w = Wrapper(NamedPoint("deep", Point(int32(9), int32(8))))
    print(asdict(w))

    # 8. Default values
    m = MaybeNamed("test")
    print(asdict(m))

    # 9. Empty list
    c = Container([])
    print(asdict(c))

    # 10. Multiple list fields (mixed field types)
    ml = MultiList([Point(int32(1), int32(2))], ["a", "b"])
    print(asdict(ml))

    # 11. Dict with dataclass values
    dd = DictOfDC({"origin": Point(int32(0), int32(0)), "end": Point(int32(1), int32(2))})
    print(asdict(dd))
    print(astuple(dd))

    # 12. Tuple with mixed types (DC + scalar)
    tm = TupleMixed((Point(int32(1), int32(2)), int32(42)))
    print(asdict(tm))
    print(astuple(tm))

    # 13. Tuple with all dataclass elements
    ta = TupleAllDC((Point(int32(1), int32(2)), Point(int32(3), int32(4))))
    print(asdict(ta))
    print(astuple(ta))

main()
