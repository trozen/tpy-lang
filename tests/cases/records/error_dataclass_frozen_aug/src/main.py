# Error: cannot use augmented assignment on frozen dataclass field
from dataclasses import dataclass
from tpy import Int32

@dataclass(frozen=True)
class Point:
    x: Int32
    y: Int32

def main() -> None:
    p = Point(1, 2)
    p.x += 1  # tpyc: error(/Cannot assign to field 'x' of frozen dataclass 'Point'/)

main()
