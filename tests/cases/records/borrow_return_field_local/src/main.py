# A method returning a reference-typed FIELD by borrow, bound to a LOCAL and
# then mutated: the local aliases the field, so the append is visible through
# the receiver. Two legs of one shape -- the arm is shared, so the bytearray
# and the list read the same row.
from tpy import int32


class H:
    buf: bytearray
    xs: list[int32]

    def __init__(self) -> None:
        self.buf = bytearray(b"ab")
        self.xs = [1, 2]

    def view(self) -> bytearray:
        return self.buf  # tpyc: ok

    def nums(self) -> list[int32]:
        return self.xs  # tpyc: ok


def main() -> None:
    h = H()
    # The binding under test: a borrow-returned bytearray in a plain local.
    v = h.view()
    v.append(33)
    print(len(h.buf), len(v))
    ns = h.nums()
    ns.append(3)
    print(len(h.xs), len(ns))


main()
