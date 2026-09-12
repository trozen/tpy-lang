# Error: mutable dataclass cannot be used as dict key (no __hash__)
from dataclasses import dataclass
from tpy import int32

@dataclass
class Point:
    x: int32
    y: int32

def main() -> None:
    d: dict[Point, str] = {Point(1, 2): "a"}  # tpyc: error(/cannot be used as a dict key.*missing __hash__/)

main()
