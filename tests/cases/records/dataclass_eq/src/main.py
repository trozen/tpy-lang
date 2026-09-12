# @dataclass auto-generated __eq__ (field-by-field comparison)
from dataclasses import dataclass
from tpy import int32
from typing import Optional

@dataclass
class Id:
    value: int32

@dataclass
class Point:
    x: int32
    y: int32

@dataclass
class Config:
    name: str
    value: int32
    label: Optional[str] = None

def main() -> None:
    # Single field
    print(Id(1) == Id(1))
    print(Id(1) == Id(2))

    p1 = Point(1, 2)
    p2 = Point(1, 2)
    p3 = Point(1, 3)
    print(p1 == p2)
    print(p1 == p3)
    print(p1 != p3)

    c1 = Config("a", 1)
    c2 = Config("a", 1)
    c3 = Config("a", 1, "x")
    print(c1 == c2)
    print(c1 == c3)

main()
