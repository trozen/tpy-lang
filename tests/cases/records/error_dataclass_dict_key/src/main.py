# Error: mutable dataclass cannot be used as dict key (no __hash__)
from dataclasses import dataclass
from tpy import Int32

@dataclass
class Point:
    x: Int32
    y: Int32

def main() -> None:
    d: dict[Point, str] = {Point(1, 2): "a"}  # tpyc: error(/cannot be used as a dict key.*missing __hash__/)

main()
