# `a, b = <container element>` / `= <field>` where the tuple has a
# REFERENCE element and the receiver binds CONST (a param no body
# mutates): the lift spells `const T*` element pointers. The mutating
# twin is in the same case so the two spellings sit side by side -- there the
# unpacked element ALIASES the container, and the write is read back through
# the container to prove it is not a copy. The resumable positions are NOT
# covered: a frame unpack of the same source copies the tuple into a case
# block and points the frame field into it (BUGS.md#frame-tuple-unpack-elem-copy).
from tpy import int32


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Holder:
    pair: tuple[int32, Box]

    def __init__(self, b: Box) -> None:
        self.pair = (1, b)

    # method: the receiver is `self`, const because the body only reads
    def total(self) -> int32:
        a, b = self.pair  # tpyc: ok
        return a + b.n


def read_only(pairs: list[tuple[int32, Box]]) -> int32:
    # free function: `pairs` takes the const-borrow verdict, so the lift
    # spells `const Box*`
    a, b = pairs[0]  # tpyc: ok
    return a + b.n


def mutating(pairs: list[tuple[int32, Box]]) -> int32:
    # the same source, written through: the param drops the const verdict and
    # the element aliases the container
    a, b = pairs[0]  # tpyc: ok
    b.n += 10
    return a + b.n


def field_read(h: Holder) -> int32:
    # field source off a const receiver
    a, b = h.pair  # tpyc: ok
    return a + b.n


def main() -> None:
    xs = [(1, Box(2))]
    print("read", read_only(xs))
    print("mut", mutating(xs), xs[0][1].n)
    print("field", field_read(Holder(Box(5))))
    print("method", Holder(Box(6)).total())


main()
