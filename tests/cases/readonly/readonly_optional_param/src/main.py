# readonly[T | None] and readonly[T] | None both work, with None narrowing.
from tpy import Int32, readonly

class Point:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x

# Form 1: readonly[T | None]
def f1(p: readonly[Point | None]) -> Int32:
    if p is not None:
        return p.x
    return Int32(0)

# Form 2: readonly[T] | None
def f2(p: readonly[Point] | None) -> Int32:
    if p is not None:
        return p.x
    return Int32(0)

def main() -> None:
    p = Point(Int32(42))
    print(f1(p))
    print(f2(p))
    print(f1(None))
    print(f2(None))

main()
