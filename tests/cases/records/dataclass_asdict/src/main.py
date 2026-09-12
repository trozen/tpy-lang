# Test asdict() on a flat dataclass
from dataclasses import dataclass, asdict
from tpy import int32

@dataclass
class Point:
    x: int32
    y: int32

def main() -> None:
    p = Point(int32(1), int32(2))
    d = asdict(p)
    print(d)
    print(d["x"])
    print(d["y"])

main()
