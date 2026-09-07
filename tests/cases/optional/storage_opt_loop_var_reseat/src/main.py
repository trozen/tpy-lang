# A reassigned Optional target fed from a storage-optional loop variable reseats
# through the SAME pointer lift, never a bare pointer copy.
from tpy import Int32, readonly


class Point:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x


class Holder:
    pairs: list[Point | None]

    def __init__(self) -> None:
        self.pairs = [Point(1), None]

    @readonly
    def probe(self) -> Int32:
        first: Point | None = None
        for it in self.pairs:
            first = it  # reseats through the optional-to-pointer lift
            if first is not None:
                return first.x
        return -1

    def bump(self) -> None:
        first: Point | None = None
        for it in self.pairs:
            first = it
            if first is not None:
                # The reseated binding BORROWS the element, so this write
                # lands in the holder's own list.
                first.x = 9
                return


def main() -> None:
    h = Holder()
    print(h.probe())
    h.bump()
    print(h.probe())


main()
