# Test fully qualified dataclasses.dataclass, dataclasses.asdict, dataclasses.astuple
import dataclasses
from tpy import Int32

@dataclasses.dataclass
class Point:
    x: Int32
    y: Int32

def main() -> None:
    p = Point(Int32(1), Int32(2))
    d = dataclasses.asdict(p)
    print(d)
    t = dataclasses.astuple(p)
    print(t)

main()
