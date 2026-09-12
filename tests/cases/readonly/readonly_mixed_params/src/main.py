# readonly[T] and mutable T params coexist -- src is const, dest is mutable.
from tpy import int32, readonly

class Point:
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

def copy_into(src: readonly[Point], dest: Point) -> None:
    dest.x = src.x
    dest.y = src.y

def main() -> None:
    a = Point(int32(10), int32(20))
    b = Point(int32(0), int32(0))
    copy_into(a, b)
    print(b.x)
    print(b.y)

main()
