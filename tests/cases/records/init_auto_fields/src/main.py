# Auto-declare fields from __init__ parameter assignments (no class-level annotations)
from tpy import int32


class Point:
    def __init__(self, x: int32, y: int32):
        self.x = x
        self.y = y


def main() -> None:
    p = Point(int32(10), int32(20))
    print(p.x)
    print(p.y)


main()
