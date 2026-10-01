# Unpacking a tuple through a NAME when the tuple mixes a borrowed and an
# owned record: the local holds the same borrow form a mixed param does, so
# the unpack lowers like the param's -- the borrowed target aliases the
# caller's object and the owned one is the local's own.
from tpy import int32, Own, copy


class Point:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


def split(p: Point) -> tuple[Point, Own[Point]]:
    return (p, copy(p))


def main() -> None:
    p = Point(1)
    t = split(p)
    # The tuple arrives through a NAME here, not straight off the call.
    ref, owned = t  # tpyc: ok
    ref.x = 5
    owned.x = 7
    # the borrowed target wrote through to `p`; the owned one did not
    print(p.x, ref.x, owned.x)


main()
