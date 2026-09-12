# Test asdict()/astuple() with mixed types, nested dataclass, and list recursion
from dataclasses import dataclass, asdict, astuple
from tpy import int32

@dataclass
class Point:
    x: int32
    y: int32

@dataclass
class Person:
    name: str
    age: int32

@dataclass
class NamedPoint:
    name: str
    pos: Point

@dataclass
class Group:
    label: str
    members: list[Point]

def main() -> None:
    # Mixed types: dict[str, str|int32]
    p = Person("Alice", int32(30))
    print(asdict(p))
    print(astuple(p))

    # Mixed with nested dataclass
    np = NamedPoint("origin", Point(int32(0), int32(0)))
    print(asdict(np))

    # List of dataclasses: recursed into list of dicts
    g = Group("pts", [Point(int32(1), int32(2)), Point(int32(3), int32(4))])
    print(asdict(g))

main()
