# A `Callable` returns a class instance by value: a local bound from it dies at
# the return (BUGS.md#callable-value-borrow-return-copies-unwarned).
from typing import Callable
from tpy import readonly


class Node:
    def __init__(self, v: int) -> None:
        self.v = v


def apply(f: Callable[[Node], readonly[Node]], n: Node) -> readonly[Node]:
    x = f(n)
    return x  # tpyc: error(/Cannot return local or temporary as reference/)


def ident(n: Node) -> readonly[Node]:
    return n


def main() -> None:
    print(apply(ident, Node(5)).v)


main()
