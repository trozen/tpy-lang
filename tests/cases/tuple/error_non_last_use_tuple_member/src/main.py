# A name whose use in an owned tuple literal is NOT its last (it feeds two
# members) is not a move source for the first, so the return rejects.
from tpy import int32, Own


class Node:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


def dup() -> Own[tuple[Node, Node]]:
    n = Node(1)
    return (n, n)  # tpyc: error(/return.tuple_source/)


def main() -> None:
    print(dup()[0].v)


main()
