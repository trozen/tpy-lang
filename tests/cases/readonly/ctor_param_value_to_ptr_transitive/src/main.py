# Sibling of `ctor_param_value_to_ptr_call/` with one extra hop: ctor's
# `Node` param is mutated transitively via a helper, not directly.
from tpy import Ptr


class Node:
    x: int

    def __init__(self) -> None:
        self.x = 0


def take_mut(p: Ptr[Node]) -> None:
    p.x = 1


def helper(n: Node) -> None:
    take_mut(n)


class Holder:
    started: bool

    def __init__(self, node: Node) -> None:
        helper(node)
        self.started = True


def main() -> None:
    n = Node()
    h = Holder(n)
    print(n.x)
    print(h.started)


main()
