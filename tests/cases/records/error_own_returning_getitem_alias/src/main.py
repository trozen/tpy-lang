# A by-VALUE (`Own`) `__getitem__` result is an rvalue with no alias to bind, so
# the reference-alias declaration rejects.
from tpy import Int32, Own


class Node:
    v: Int32

    def __init__(self, v: Int32) -> None:
        self.v = v


class Maker:
    def __init__(self) -> None:
        pass

    def __getitem__(self, i: Int32) -> Own[Node]:
        return Node(i)


def take(m: Maker) -> Int32:
    n = m[0]  # tpyc: error(/decl.slot_type/)
    return n.v


def main() -> None:
    print(take(Maker()))


main()
