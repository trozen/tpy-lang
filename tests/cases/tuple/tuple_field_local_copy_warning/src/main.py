# Tuple field assignment with body-local sources: per-element warn-on-copy
# fires only for non-last-use elements (last-use ones auto-move silently).
# Parallel of `auto_move/warn_own_local_field_copy::test_non_last_use` for
# the tuple case, and complement of `tuple_field_copy_warning` (which uses
# params for both elements).
from tpy import int32


class Point:
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y


class Mixed:
    pp: tuple[Point, Point]

    def __init__(self) -> None:
        a = Point(int32(1), int32(2))
        b = Point(int32(3), int32(4))
        self.pp = (a, b)  # tpyc: warning(/copies Point into field \(tuple element 0\)/)
        print(a.x)  # later use of `a` -- forces element 0 to copy


def main() -> None:
    m = Mixed()
    print(m.pp[0].x)
    print(m.pp[1].x)


main()
