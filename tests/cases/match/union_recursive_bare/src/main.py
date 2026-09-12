# match over a recursive union alias with BARE class-pattern arms (no field
# sub-patterns): the wrapper subject dispatches on its .value variant index.
from tpy import int32


class Leaf:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


type Tree = Leaf | list[Tree]


def head(t: Tree) -> int32:
    match t:
        case Leaf():
            return t.v
        case _:
            return -1


def main() -> None:
    a: Tree = Leaf(42)
    b: list[Tree] = [Leaf(1)]
    print(head(a))
    print(head(b))


main()
