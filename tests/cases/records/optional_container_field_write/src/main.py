# An `Optional[<reference type>]` FIELD written from a bare NAME. The field
# is storage form (`std::optional<std::vector<uint8_t>>` for the bytearray
# leg, `std::optional<std::vector<T>>` for the list ones), so the name lands
# in it bare or moved whichever family it is.
#
# Two boundaries, deliberately different: an `Own` param and a local at its
# last use MOVE into the slot (the source is consumed, so the buffer the
# field holds is the caller's), while a plain borrow param is the warned
# COPY every reference type gets at owned storage.
#
# The `Box` leg pins the MOVE half as a move: `Box` is @nocopy, so if that
# write copied instead of moving, the case would not compile at all. The
# bytearray leg cannot carry that check itself -- its elements are UInt8 --
# so the @nocopy payload rides the list sibling of the same arm.
from tpy import Int32, Own
from tplib.box import Box


class Slot:
    b: bytearray | None
    xs: list[Int32] | None
    boxes: list[Box[Int32]] | None

    def __init__(self) -> None:
        self.b = None
        self.xs = None
        self.boxes = None

    def take(self, v: Own[bytearray]) -> None:
        self.b = v  # tpyc: ok

    def take_list(self, v: Own[list[Int32]]) -> None:
        self.xs = v  # tpyc: ok

    def take_boxes(self, v: Own[list[Box[Int32]]]) -> None:
        self.boxes = v  # tpyc: ok -- a copy here would not compile

    def copy_in(self, v: bytearray) -> None:
        self.b = v  # tpyc: warning(/copies bytearray into field/)

    def size(self) -> Int32:
        if self.b is None:
            return -1
        return len(self.b)

    def grow(self) -> None:
        if self.b is not None:
            self.b.append(90)


def read_bound(s: Slot) -> Int32:
    # The READ side of the same slot: the storage-form optional field
    # lifts through `optional_to_ptr` into a plain local, so the narrowed
    # binding ALIASES the field and `grow()` is visible through it.
    d = s.b  # tpyc: ok
    if d is None:
        return -1
    s.grow()
    return len(d)


def local_source(s: Slot) -> None:
    v = bytearray(b"ab")
    s.b = v  # tpyc: ok


def main() -> None:
    s = Slot()
    print(s.size())
    local_source(s)
    print(s.size())
    # The moved-in buffer is the field's own and is still mutable through it.
    s.grow()
    print(s.size())
    owned = bytearray(b"xyz")
    s.take(owned)
    print(s.size())
    s.grow()
    print(s.size())
    borrowed = bytearray(b"q")
    s.copy_in(borrowed)
    print(s.size())
    print(read_bound(s))
    nums = [1, 2, 3]
    s.take_list(nums)
    if s.xs is not None:
        print(len(s.xs))
    boxes: list[Box[Int32]] = [Box(4), Box(5)]
    s.take_boxes(boxes)
    if s.boxes is not None:
        print(len(s.boxes))


main()
