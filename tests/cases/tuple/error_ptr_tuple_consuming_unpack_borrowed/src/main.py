# A consuming unpack before a loop's back edge rebinds the tuple to borrowed storage is
# refused, located (BUGS.md#ptr-tuple-consuming-unpack-borrowed-element-rejects).
from tpy import Own, int32


class Box:
    xs: list[int32]

    def __init__(self, n: int32) -> None:
        self.xs = [n]


class H:
    tp: tuple[Box, Box]

    def __init__(self) -> None:
        self.tp = (Box(7), Box(8))


def make_mixed(n: int32, b: Box) -> tuple[Own[Box], Box]:
    return (Box(n), b)


def first(h: H, b: Box, n: int32) -> Own[Box]:
    p = make_mixed(1, b)
    i = 0
    while i < n:
        if i == 1:
            a, z = p  # tpyc: error(/element 0 of 'p' is borrowed from other storage by a binding of 'p'.*use \(copy\(p\[0\]\), p\[1\]\) there instead/)
            return a
        p = h.tp
        i += 1
    return Box(0)


def main() -> None:
    r = first(H(), Box(5), 3)
    print(len(r.xs))


main()
