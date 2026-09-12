# readonly[T | None] and readonly[T] | None both work, with None narrowing.
from tpy import int32, readonly

class Point:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x

# Form 1: readonly[T | None]
def f1(p: readonly[Point | None]) -> int32:
    if p is not None:
        return p.x
    return int32(0)

# Form 2: readonly[T] | None
def f2(p: readonly[Point] | None) -> int32:
    if p is not None:
        return p.x
    return int32(0)

def main() -> None:
    p = Point(int32(42))
    print(f1(p))
    print(f2(p))
    print(f1(None))
    print(f2(None))

main()
