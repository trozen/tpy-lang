# @dataclass(frozen=True): immutable instances with auto __init__ and __eq__
from dataclasses import dataclass
from tpy import Int32

@dataclass(frozen=True)
class Point:
    x: Int32
    y: Int32

@dataclass(frozen=True)
class Config:
    name: str
    value: Int32

def main() -> None:
    p = Point(1, 2)
    print(p)
    print(p.x, p.y)
    print(p == Point(1, 2))
    print(p == Point(3, 4))

    c = Config("test", 42)
    print(c)

main()
