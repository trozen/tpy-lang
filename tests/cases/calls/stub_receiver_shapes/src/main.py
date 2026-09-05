# The builtin-stub receiver family's SHAPES, one leg each: a bare NAME, a
# FIELD, a container-valued PROPERTY (the getter call is the receiver
# lvalue), an inner-CALL receiver, and an inherited stub method on a subclass
# of the builtin. Every shape is spelled for a `bytearray` and for a `list`,
# because bytearray rides this family rather than one of its own -- if it had
# its own, the two columns could disagree.
from tpy import Int32, Own


class Buf:
    data: bytearray
    nums: list[Int32]

    def __init__(self, data: Own[bytearray]) -> None:
        self.data = data
        self.nums = [1]

    @property
    def view(self) -> bytearray:
        return self.data

    @property
    def rows(self) -> list[Int32]:
        return self.nums


class Tagged(bytearray):
    tag: Int32

    def __init__(self) -> None:
        self.tag = 7


class TaggedList(list[Int32]):
    tag: Int32

    def __init__(self) -> None:
        self.tag = 8


def main() -> None:
    ba = bytearray(b"ab")
    ba.append(99)  # tpyc: ok
    seed = bytearray(b"xy")
    holder = Buf(seed)
    holder.data.append(100)  # tpyc: ok
    holder.view.append(101)  # tpyc: ok
    # The property lends the buffer rather than handing back a copy: both
    # appends above have to be visible through the FIELD read.
    print(len(holder.data))
    print(holder.data[2])
    print(holder.data[3])
    # An inner-call receiver: `strip()` yields an owned temporary that the
    # outer stub method composes onto.
    print(ba.strip().upper())  # tpyc: ok
    t = Tagged()
    t.append(65)  # tpyc: ok
    t.append(66)  # tpyc: ok
    print(len(t))
    print(t.tag)
    print(ba)
    # The same five shapes one family over.
    xs = [1]
    xs.append(2)  # tpyc: ok
    holder.nums.append(3)  # tpyc: ok
    holder.rows.append(4)  # tpyc: ok
    print(len(holder.nums))
    print(xs.copy().index(2))  # tpyc: ok -- an inner-call receiver
    tl = TaggedList()
    tl.append(5)  # tpyc: ok
    print(len(tl), tl.tag)


main()
