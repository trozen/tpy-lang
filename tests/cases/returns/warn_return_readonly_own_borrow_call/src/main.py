# `readonly[Own[T]]` is the same owning return slot with a const view on top,
# so the borrowed-source rule applies there too -- the spelling is not a way
# past it, and the slot copies with the same warning. This leg is the
# borrow-returning CALL; the FIELD leg is in the container twin. The copy is
# the ACKNOWLEDGED CPython divergence, so the case prints only what both agree
# on: `updated()` mutates through the borrow BEFORE the return.
from tpy import Int32, Own, readonly, copy


class Point:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x

    def updated(self) -> 'Point':
        self.x += 1
        return self


def bump(p: Point) -> readonly[Own[Point]]:
    return p.updated()  # tpyc: warning(/copies Point into owned storage/)


def bump_copy(p: Point) -> readonly[Own[Point]]:
    return copy(p.updated())  # tpyc: ok


def main() -> None:
    p = Point(1)
    print(bump(p).x, p.x)
    print(bump_copy(p).x, p.x)


main()
