# Inverse guard: a comprehension of default-constructible, copyable elements
# keeps the stack Array optimization (must not be forced to list).
from tpy import Int32


class Point:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x


def main() -> None:
    nums = [i for i in range(4)]  # tpyc: type(/Array\[Int32, 4\]/)
    pts = [Point(i) for i in range(4)]  # tpyc: type(/Array\[Point, 4\]/)
    print(len(nums) + len(pts))


main()
