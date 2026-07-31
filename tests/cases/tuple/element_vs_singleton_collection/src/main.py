# A tuple ELEMENT at a container position must behave as the same type would as
# a SINGLETON there. A container owns its elements, so a borrowed reference
# copies into it either way -- each pair below stores, mutates through the
# container, and reads the original back, so an alias would show as the written
# value instead of 2.
from tpy import Int32


class Box:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


def make_borrow(b: Box) -> tuple[Box, Box]:
    return (b, b)


def singleton_append(b: Box) -> Int32:
    xs: list[Box] = []
    xs.append(b)  # tpyc: warning(/copies Box into owned storage/)
    xs[0].n = 21
    return b.n


def tuple_append(b: Box) -> Int32:
    xs: list[tuple[Box, Box]] = []
    xs.append(make_borrow(b))  # tpyc: warning(/copies Box into owned storage/) warning(/copies Box into owned storage/)
    xs[0][0].n = 22
    return b.n


def singleton_dict(b: Box) -> Int32:
    d: dict[Int32, Box] = {}
    d[1] = b  # tpyc: warning(/copies Box into container/)
    d[1].n = 23
    return b.n


def tuple_dict(b: Box) -> Int32:
    d: dict[Int32, tuple[Box, Box]] = {}
    d[1] = make_borrow(b)  # tpyc: warning(/copies Box into container/) warning(/copies Box into container/)
    d[1][0].n = 24
    return b.n


def main() -> None:
    # All 2: the container owns its element, so the write hit the copy.
    print(singleton_append(Box(2)), tuple_append(Box(2)))
    print(singleton_dict(Box(2)), tuple_dict(Box(2)))


main()
