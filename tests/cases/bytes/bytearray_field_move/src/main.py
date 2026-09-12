# A reference-typed FIELD written from an owned local at its LAST USE: the
# merged field-write arm spells `this->f = std::move(fresh);`. The bytearray
# leg is the reason the arm carries an explicit materialize decision -- a
# bytes-family reference member shares `bytes`' owned-buffer storage, so
# without the decision the same convert would be read as the view->owned
# copy and the emitter would refuse it. The list leg is the contrast: the
# identical row with the decision left to the emitter, and the @nocopy leg
# beside it is what proves the row MOVES: mutating through the field after
# the write only shows the field owns *a* buffer, while a `Box` element makes
# a copy at that boundary a C++ compile error. The bytearray leg cannot carry
# that check itself (its elements are uint8), so it rides the list sibling of
# the same arm.
from tpy import int32
from tplib.box import Box


class Buf:
    data: bytearray
    tags: list[int32]
    boxes: list[Box[int32]]

    def __init__(self) -> None:
        self.data = bytearray()
        self.tags = []
        self.boxes = []

    def reset(self, n: int32) -> None:
        fresh = bytearray(n)
        self.data = fresh  # tpyc: ok -- the moved bytes-family name

    def retag(self, n: int32) -> None:
        fresh: list[int32] = [n]
        self.tags = fresh  # tpyc: ok -- the same row, container leg

    def rebox(self, n: int32) -> None:
        fresh: list[Box[int32]] = [Box(n)]
        self.boxes = fresh  # tpyc: ok -- a copy here would not compile


def main() -> None:
    b = Buf()
    b.reset(3)
    # Mutating THROUGH the field after the move proves the field owns the
    # buffer the local built, rather than a copy of it.
    b.data[0] = 65
    b.data.append(66)
    print(len(b.data), b.data[0], b.data[3])
    b.retag(7)
    b.tags.append(8)
    print(len(b.tags), b.tags[0], b.tags[1])
    b.rebox(9)
    print(len(b.boxes))


main()
