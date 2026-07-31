# A mixed owned+borrow tuple PARAM is a const& borrow of the mixed render
# (`const std::tuple<Box, const Box*>&`), NOT owning storage -- only a fully
# owned tuple param takes the storage/`&&` ABI. So a mixed call result passes
# straight into it with no form conversion in either direction: it is already
# the borrow form the slot wants, and lifting it to storage would hand the slot
# the wrong shape.
from tpy import Int32, Own


class Box:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


def make_mixed(b: Box) -> tuple[Own[Box], Box]:
    return (Box(1), b)


def make_owned() -> tuple[Own[Box], Own[Box]]:
    return (Box(10), Box(20))


def take_mixed(p: tuple[Own[Box], Box]) -> Int32:
    return p[0].n + p[1].n


def take_owned(p: tuple[Own[Box], Own[Box]]) -> Int32:
    return p[0].n + p[1].n


def relay(b: Box) -> Int32:
    # The param forwards onward as a param -- still no conversion.
    return take_mixed(make_mixed(b))


def mutate_borrowed(p: tuple[Own[Box], Box]) -> None:
    # The borrowed element is a NON-const pointer inside the const tuple
    # (`const std::tuple<Box, Box*>&`), so this reaches the caller's object --
    # a param borrows, it does not copy, matching CPython.
    p[1].n = 42


def main() -> None:
    b = Box(2)
    print(take_mixed(make_mixed(b)))
    print(relay(b))
    # The fully-owned sibling keeps the storage/&& ABI and moves in.
    print(take_owned(make_owned()))
    # The borrowed element really is borrowed, not copied: the write inside the
    # callee lands on `b`, so this prints 42 under TPy and CPython alike.
    mutate_borrowed(make_mixed(b))
    print(b.n)


main()
