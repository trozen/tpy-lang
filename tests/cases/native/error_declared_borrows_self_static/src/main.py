# `borrows=("self",)` on a static method stub names no parameter: a static
# method has no receiver for its result to borrow.
from tpy import int32
from tpy.extern import native


@native
class Node:
    v: int32

    # the declaration names a receiver the method does not have
    @staticmethod
    @native(borrows=("self",))
    def make(other: Node) -> Node: ...  # tpyc: error(/'self', which names no parameter of the static method/)


def main() -> None:
    n = Node(1)
    print(Node.make(n).v)


main()
