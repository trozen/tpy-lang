# Subject-storage mutation in a GUARD warns when a non-scalar binding (here a
# record field) aliases the subject. Runtime: the guard's pop empties only the
# tail, so the bound head stays valid.
from tpy import int32


class Tag:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Item:
    tag: Tag
    v: int32

    def __init__(self, n: int32, v: int32) -> None:
        self.tag = Tag(n)
        self.v = v


def poke(xs: list[Item]) -> None:
    match xs[0]:
        case Item(tag=t) if xs.pop().v > 100:  # tpyc: warning(/'xs\[0\]' is mutated in this arm while pattern bindings borrow/)
            print("big tail", t.n)
        case Item(tag=t2):
            print("small", t2.n)


def main() -> None:
    poke([Item(1, 5), Item(2, 50)])


main()
