# Field access reached through several Ptr hops, and a for-each over a
# container member of a Ptr binding -- each hop spells its own deref_check.
from tpy import Int32, Own, Ptr


class Sector:
    floor_h: Int32

    def __init__(self, floor_h: Int32) -> None:
        self.floor_h = floor_h


class Seg:
    sector_front: Ptr[Sector]

    def __init__(self, sector_front: Ptr[Sector]) -> None:
        self.sector_front = sector_front


class SubSector:
    segs: list[Ptr[Seg]]

    def __init__(self, segs: Own[list[Ptr[Seg]]]) -> None:
        self.segs = segs


class Map:
    sectors: list[Sector]
    segs: list[Seg]
    subsectors: list[SubSector]

    def __init__(self) -> None:
        self.sectors = [Sector(42), Sector(7)]
        self.segs = [Seg(self.sectors[0]), Seg(self.sectors[1])]
        segs: list[Ptr[Seg]] = []
        segs.append(self.segs[0])
        segs.append(self.segs[1])
        self.subsectors = [SubSector(segs)]


def head_floor(subsectors: list[Ptr[SubSector]]) -> Int32:
    # Two Ptr hops under a subscript: the whole chain is one read.
    return subsectors[0].segs[0].sector_front.floor_h  # tpyc: ok


def via_local(ss: Ptr[SubSector]) -> Int32:
    seg = ss.segs[0]
    # A Ptr-valued field taken off a Ptr binding, then read again.
    return seg.sector_front.floor_h  # tpyc: ok


def total(subsectors: list[Ptr[SubSector]]) -> Int32:
    acc = 0
    for subsector in subsectors:
        # A container member of a Ptr binding is the loop's iterable.
        for seg in subsector.segs:  # tpyc: ok
            acc += seg.sector_front.floor_h
    return acc


def raise_floors(subsectors: list[Ptr[SubSector]]) -> None:
    for subsector in subsectors:
        for seg in subsector.segs:
            # The chain is an lvalue: the write lands on the pointee.
            seg.sector_front.floor_h += 1  # tpyc: ok


def main() -> None:
    m = Map()
    subsectors: list[Ptr[SubSector]] = []
    subsectors.append(m.subsectors[0])
    print(head_floor(subsectors))
    print(via_local(subsectors[0]))
    print(total(subsectors))
    raise_floors(subsectors)
    # The mutation reached the sectors the pointers alias, not a copy.
    print(m.sectors[0].floor_h, m.sectors[1].floor_h)


main()
