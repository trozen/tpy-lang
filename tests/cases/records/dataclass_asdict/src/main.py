# Test asdict() on a flat dataclass
from dataclasses import dataclass, asdict
from tpy import Int32

@dataclass
class Point:
    x: Int32
    y: Int32

def main() -> None:
    p = Point(Int32(1), Int32(2))
    d = asdict(p)
    print(d)
    print(d["x"])
    print(d["y"])

main()
