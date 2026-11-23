# A stub's reference result may be its argument, as its TPy twin's may:
# returning it over a local is refused like the twin's `return n`.
from tpy import int32
from tpy.extern import cpp_template


class Node:
    def __init__(self, v: int32) -> None:
        self.v = v


@cpp_template("::identity({0})")
def same(n: Node) -> Node: ...


def escape() -> Node:
    x = Node(9)
    return same(x)  # tpyc: error(/Cannot return local or temporary as reference/)


def main() -> None:
    print(escape().v)


main()
