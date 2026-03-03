# Ternary expression: pointer-repr Optional (Optional[Record])
from typing import Optional

class Point:
    def __init__(self, x: int, y: int) -> None:
        self.x = x
        self.y = y

def get_or_none(flag: bool, p: Point) -> Optional[Point]:
    # T + None -> Optional[T] where T is a record (pointer-repr)
    return p if flag else None

def pick(flag: bool, a: Optional[Point], b: Optional[Point]) -> Optional[Point]:
    # Both branches are Optional[Record]
    return a if flag else b

def narrowed_field(p: Optional[Point]) -> int:
    # is not None narrowing on pointer-repr Optional, then access field
    return p.x if p is not None else 0

def main() -> None:
    p = Point(1, 2)
    q = Point(3, 4)

    r1 = get_or_none(True, p)
    if r1 is not None:
        print(r1.x)
    r2 = get_or_none(False, p)
    print(r2)

    r3 = pick(True, p, q)
    if r3 is not None:
        print(r3.x)
    r4 = pick(False, p, q)
    if r4 is not None:
        print(r4.x)

    print(narrowed_field(p))
    print(narrowed_field(None))

main()
