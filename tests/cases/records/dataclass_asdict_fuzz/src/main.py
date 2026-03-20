from dataclasses import dataclass, asdict, astuple
from tpy import Int32

# Simple flat
@dataclass
class Point:
    x: Int32
    y: Int32

# Mixed types
@dataclass
class Person:
    name: str
    age: Int32

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
    value: Int32 = Int32(0)

# Empty list field
@dataclass
class Container:
    items: list[Point]

# Multiple list fields
@dataclass
class MultiList:
    points: list[Point]
    labels: list[str]

def main() -> None:
    # 1. Flat homogeneous
    p = Point(Int32(1), Int32(2))
    print(asdict(p))
    print(astuple(p))

    # 2. Mixed types
    person = Person("Alice", Int32(30))
    print(asdict(person))
    print(astuple(person))

    # 3. Nested dataclass (homogeneous)
    line = Line(Point(Int32(0), Int32(0)), Point(Int32(1), Int32(1)))
    print(asdict(line))
    print(astuple(line))

    # 4. Nested with mixed types
    np = NamedPoint("origin", Point(Int32(0), Int32(0)))
    print(asdict(np))

    # 5. List of dataclasses
    poly = Polygon([Point(Int32(0), Int32(0)), Point(Int32(1), Int32(0)), Point(Int32(0), Int32(1))])
    print(asdict(poly))

    # 6. Mixed types + list
    d = Drawing("sketch", [Point(Int32(1), Int32(2))])
    print(asdict(d))

    # 7. Deeply nested
    w = Wrapper(NamedPoint("deep", Point(Int32(9), Int32(8))))
    print(asdict(w))

    # 8. Default values
    m = MaybeNamed("test")
    print(asdict(m))

    # 9. Empty list
    c = Container([])
    print(asdict(c))

    # 10. Multiple list fields (mixed field types)
    ml = MultiList([Point(Int32(1), Int32(2))], ["a", "b"])
    print(asdict(ml))

main()
