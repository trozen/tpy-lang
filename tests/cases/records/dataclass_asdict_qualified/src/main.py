# Test fully qualified dataclasses.dataclass, dataclasses.asdict, dataclasses.astuple
import dataclasses
from tpy import int32

@dataclasses.dataclass
class Point:
    x: int32
    y: int32

def main() -> None:
    p = Point(int32(1), int32(2))
    d = dataclasses.asdict(p)
    print(d)
    t = dataclasses.astuple(p)
    print(t)

main()
