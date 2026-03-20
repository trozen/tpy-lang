# Test asdict()/astuple() with mixed types, nested dataclass, and list recursion
from dataclasses import dataclass, asdict, astuple
from tpy import Int32

@dataclass
class Point:
    x: Int32
    y: Int32

@dataclass
class Person:
    name: str
    age: Int32

@dataclass
class NamedPoint:
    name: str
    pos: Point

@dataclass
class Group:
    label: str
    members: list[Point]

def main() -> None:
    # Mixed types: dict[str, str|Int32]
    p = Person("Alice", Int32(30))
    print(asdict(p))
    print(astuple(p))

    # Mixed with nested dataclass
    np = NamedPoint("origin", Point(Int32(0), Int32(0)))
    print(asdict(np))

    # List of dataclasses: recursed into list of dicts
    g = Group("pts", [Point(Int32(1), Int32(2)), Point(Int32(3), Int32(4))])
    print(asdict(g))

main()
