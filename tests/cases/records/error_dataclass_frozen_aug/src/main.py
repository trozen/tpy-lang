# Error: cannot use augmented assignment on frozen dataclass field
from dataclasses import dataclass
from tpy import int32

@dataclass(frozen=True)
class Point:
    x: int32
    y: int32

def main() -> None:
    p = Point(1, 2)
    p.x += 1  # tpyc: error(/Cannot assign to field 'x' of frozen dataclass 'Point'/)

main()
