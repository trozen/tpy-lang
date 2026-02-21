# readonly[T] per-parameter: field reads and @readonly method calls work.
from tpy import Int32, readonly

class Point:
    x: Int32
    y: Int32

    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

    @readonly
    def magnitude_sq(self) -> Int32:
        return self.x * self.x + self.y * self.y

def observe(p: readonly[Point]) -> Int32:
    return p.x + p.y

def call_readonly_method(p: readonly[Point]) -> Int32:
    return p.magnitude_sq()

def main() -> None:
    p = Point(Int32(3), Int32(4))
    print(observe(p))
    print(call_readonly_method(p))

main()
