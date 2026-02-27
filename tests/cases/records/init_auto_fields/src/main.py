# Auto-declare fields from __init__ parameter assignments (no class-level annotations)
from tpy import Int32


class Point:
    def __init__(self, x: Int32, y: Int32):
        self.x = x
        self.y = y


def main() -> None:
    p = Point(Int32(10), Int32(20))
    print(p.x)
    print(p.y)


main()
