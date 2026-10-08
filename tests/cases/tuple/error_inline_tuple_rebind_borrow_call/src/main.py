# A literal-bound tuple local holding an element INLINE (a fresh-at-last-use
# name) is not the pointer tuple a borrowing call returns, so the rebind is
# refused instead of spelling an ill-formed assignment.
from tpy import int32


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def pick(xs: list[Box], b: Box) -> tuple[Box, Box]:
    return (xs[0], b)


def main() -> None:
    a = Box(1)
    b = Box(2)
    xs = [Box(90)]
    p = (a, b)
    # `a` was captured inline above: `p` is `std::tuple<Box, Box*>`.
    p = pick(xs, b)  # tpyc: error(/reseat.borrow_tuple_source/)
    print(p[0].n)


main()
