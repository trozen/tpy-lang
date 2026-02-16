# readonly[T] and mutable T params coexist -- src is const, dest is mutable.
from tpy import Int32, readonly

class Point:
    x: Int32
    y: Int32

    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

def copy_into(src: readonly[Point], dest: Point) -> None:
    dest.x = src.x
    dest.y = src.y

def main() -> None:
    a = Point(Int32(10), Int32(20))
    b = Point(Int32(0), Int32(0))
    copy_into(a, b)
    print(b.x)
    print(b.y)

main()
