# readonly[T] per-parameter: field reads and @readonly method calls work.
from tpy import int32, readonly

class Point:
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

    @readonly
    def magnitude_sq(self) -> int32:
        return self.x * self.x + self.y * self.y

def observe(p: readonly[Point]) -> int32:
    return p.x + p.y

def call_readonly_method(p: readonly[Point]) -> int32:
    return p.magnitude_sq()

def main() -> None:
    p = Point(int32(3), int32(4))
    print(observe(p))
    print(call_readonly_method(p))

main()
