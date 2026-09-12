# Inverse guard: a comprehension of default-constructible, copyable elements
# keeps the stack Array optimization (must not be forced to list).
from tpy import int32


class Point:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


def main() -> None:
    nums = [i for i in range(4)]  # tpyc: type(/Array\[int32, 4\]/)
    pts = [Point(i) for i in range(4)]  # tpyc: type(/Array\[Point, 4\]/)
    print(len(nums) + len(pts))


main()
