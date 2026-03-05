# Error: cannot assign to field of frozen dataclass from outside
from dataclasses import dataclass
from tpy import Int32

@dataclass(frozen=True)
class Point:
    x: Int32
    y: Int32

def main() -> None:
    p = Point(1, 2)
    p.x = 3  # tpyc: error(/Cannot assign to field 'x' of frozen dataclass 'Point'/)

main()
