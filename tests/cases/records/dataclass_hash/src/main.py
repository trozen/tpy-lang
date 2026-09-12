# @dataclass(frozen=True) auto __hash__: enables hash() and dict keys
from dataclasses import dataclass
from tpy import int32

@dataclass(frozen=True)
class Point:
    x: int32
    y: int32

def main() -> None:
    p = Point(1, 2)
    # hash() works
    print(hash(p) == hash(Point(1, 2)))
    print(hash(p) == hash(Point(3, 4)))
    # dict key usage
    d: dict[Point, str] = {Point(1, 2): "a", Point(3, 4): "b"}
    print(d[Point(1, 2)])
    print(d[Point(3, 4)])
    print(len(d))

main()
