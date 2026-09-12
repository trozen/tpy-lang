# Unpacking a tuple through a NAME when the tuple mixes a borrowed and an
# owned record: TPy lowers the direct call-unpack form only, so this rejects.
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
    ref, owned = t  # tpyc: error(/stmt.tuple_unpack/)
    print(ref.x, owned.x)


main()
