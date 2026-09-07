# A NARROWED Optional field as the comprehension iterable: the route types on the
# DECLARED field type, so the narrowed unwrap has no row.
# TPy rejects `[v for v in h.items]` here today.
from tpy import Int32


class Holder:
    items: list[Int32] | None

    def __init__(self) -> None:
        self.items = None


def size(h: Holder) -> Int32:
    if h.items is not None:
        xs = [v for v in h.items]  # tpyc: error(/expr.list_comp/)
        return len(xs)
    return 0


def main() -> None:
    print(size(Holder()))


main()
