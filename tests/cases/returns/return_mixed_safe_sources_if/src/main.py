# Merging two independently-safe sources across if/else branches.
# One branch binds a local from a parameter (param-derived); the
# other binds the same local from a non-dangling call return
# (trusted). Either source alone is safe to return, and the merge
# must preserve that.

from tpy import Int32, StrView


class Point:
    def __init__(self, x: Int32) -> None:
        self.x = x


def trusted(p: Point) -> Point:
    return p


def pick_view(s: StrView) -> StrView:
    return s


def mixed_record(seed: Point, flag: bool) -> Point:
    if flag:
        result = seed            # param-derived
    else:
        result = trusted(seed)   # trusted call return
    return result  # tpyc: ok


def mixed_record_swapped(seed: Point, flag: bool) -> Point:
    if flag:
        result = trusted(seed)   # trusted call return
    else:
        result = seed            # param-derived
    return result  # tpyc: ok


def mixed_strview(p: str, flag: bool) -> StrView:
    if flag:
        sv: StrView = p                 # param-derived
    else:
        sv = pick_view(StrView("x"))    # trusted call return (literal-rooted)
    return sv  # tpyc: ok


def main():
    seed = Point(7)
    print(mixed_record(seed, True).x)
    print(mixed_record(seed, False).x)
    print(mixed_record_swapped(seed, True).x)
    print(mixed_record_swapped(seed, False).x)
    print(mixed_strview("hello", True))
    print(mixed_strview("hello", False))


main()
