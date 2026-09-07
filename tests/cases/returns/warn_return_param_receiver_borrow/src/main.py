# The NAMED-receiver spelling of warn_return_temp_receiver_borrow: a
# `p.updated()` self-borrow off a PARAM receiver is a borrowed source at the
# owning return slot, so it copies and warns -- the same one rule the
# temporary receiver takes. The copy is the ACKNOWLEDGED CPython divergence
# (CPython hands back the caller's Point), so the case prints only what both
# agree on: `updated()` mutates through the borrow BEFORE the return.
from tpy import Int32, Own, copy


class Point:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x

    def updated(self) -> 'Point':
        self.x += 1
        return self


def bump(p: Point) -> Own[Point]:
    return p.updated()  # tpyc: warning(/copies Point into owned storage/)


def bump_copy(p: Point) -> Own[Point]:
    return copy(p.updated())  # tpyc: ok


def main() -> None:
    p = Point(1)
    print(bump(p).x, p.x)
    print(bump_copy(p).x, p.x)


main()
