# Reads of a MEMBER off an explicit `Ptr[record]` binding: a container member
# subscripted, aliased, and an Optional member one Ptr-field link deeper.
from tpy import int32, Ptr, readonly


class Picture:
    width: int32

    def __init__(self, width: int32) -> None:
        self.width = width


class Sector:
    flags: list[bool]
    nums: list[int32]
    ceil_pic: Picture | None

    def __init__(self, width: int32) -> None:
        self.flags = [True, False, True]
        self.nums = [4, 5, 6]
        self.ceil_pic = Picture(width)


class Seg:
    sector_front: Ptr[Sector]

    def __init__(self, sector_front: Ptr[Sector]) -> None:
        self.sector_front = sector_front


def read_elem(sector: Ptr[Sector], i: int32) -> int32:
    return sector.nums[i]  # tpyc: ok


def read_cond(sector: Ptr[Sector], i: int32) -> int32:
    if sector.flags[i]:  # tpyc: ok
        return 10
    return 0


def grow(sector: Ptr[Sector]) -> int32:
    # The alias binds the member itself, so the append is visible through the
    # original object -- a copy here would print a stale length in main.
    fl = sector.flags  # tpyc: ok
    fl.append(True)
    return len(fl)


def count_readonly(sector: Ptr[readonly[Sector]]) -> int32:
    fl = sector.flags  # tpyc: ok
    return len(fl)


def pic_width(seg: Seg) -> int32:
    # The Optional member sits one Ptr-FIELD link deeper.
    ceil_pic = seg.sector_front.ceil_pic  # tpyc: ok
    assert ceil_pic is not None
    return ceil_pic.width


def has_pic(seg: Seg) -> bool:
    return seg.sector_front.ceil_pic is not None  # tpyc: ok


def main():
    s = Sector(64)
    p: Ptr[Sector] = s
    g = Seg(s)
    print(read_elem(p, 1), read_cond(p, 0), grow(p), count_readonly(p))
    print(len(s.flags), pic_width(g), has_pic(g))


main()
