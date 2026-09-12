# Merging two independently-safe sources across a while-loop join.
# The local is bound from one safe source before the loop and from
# a different safe source inside the loop body. Either alone is
# safe to return, and the loop-exit merge must preserve that.

from tpy import int32, StrView


class Point:
    def __init__(self, x: int32) -> None:
        self.x = x


def trusted(p: Point) -> Point:
    return p


def pick_view(s: StrView) -> StrView:
    return s


def param_then_trusted(seed: Point, n: int32) -> Point:
    result = seed                    # param-derived
    i: int32 = 0
    while i < n:
        result = trusted(seed)       # trusted call return
        i += 1
    return result  # tpyc: ok


def trusted_then_param(seed: Point, n: int32) -> Point:
    result = trusted(seed)           # trusted call return
    i: int32 = 0
    while i < n:
        result = seed                # param-derived
        i += 1
    return result  # tpyc: ok


def strview_param_then_trusted(p: str, n: int32) -> StrView:
    sv: StrView = p                            # param-derived
    i: int32 = 0
    while i < n:
        sv = pick_view(StrView("x"))           # trusted call return
        i += 1
    return sv  # tpyc: ok


def main():
    seed = Point(11)
    print(param_then_trusted(seed, 0).x)
    print(param_then_trusted(seed, 3).x)
    print(trusted_then_param(seed, 0).x)
    print(trusted_then_param(seed, 3).x)
    print(strview_param_then_trusted("hello", 0))
    print(strview_param_then_trusted("hello", 3))


main()
