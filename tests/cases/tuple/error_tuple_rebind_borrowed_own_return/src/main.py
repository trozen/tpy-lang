# An element a rebound tuple owns on one path and borrows on another, handed
# to an owning sink -- here returned into an `Own` slot: the move would take
# the caller's object and the copy sema warns about would copy the object
# the owning path could hand over, so it is refused
# (BUGS.md#rebound-tuple-borrowed-path-own-return).
from tpy import Own, int32


class Bag:
    xs: list[int32]

    def __init__(self, n: int32) -> None:
        self.xs = [n]


class H:
    pair: tuple[Bag, Bag]

    def __init__(self) -> None:
        self.pair = (Bag(50), Bag(60))


def make_pair(n: int32) -> Own[tuple[Bag, Bag]]:
    return (Bag(n), Bag(n + 1))


def first(h: H, c: bool) -> Own[Bag]:
    p = make_pair(1)
    if c:
        p = h.pair
    return p[0]  # tpyc: warning(/copies Bag into owned storage/) error(/element 0 of 'p' is an object of its own on some paths reaching here and borrowed from other storage on others.*use copy\(p\[0\]\) there instead/)


def main() -> None:
    b = first(H(), True)
    b.xs.append(1)
    print(len(b.xs))


main()
