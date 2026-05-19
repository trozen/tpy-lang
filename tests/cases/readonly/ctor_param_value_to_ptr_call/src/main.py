# Constructor params that the body passes to a Ptr[T] callee (via the
# value->Ptr coercion that takes the address) must drop the default
# `const T&` ctor-param spelling -- the address-take produces `T*`,
# not `const T*`. Mirrors the inference regular methods already get.
from tpy import Ptr


class Node:
    x: int

    def __init__(self) -> None:
        self.x = 0


def take_mut(p: Ptr[Node]) -> None:
    p.x = 1


class Holder:
    started: bool

    def __init__(self, node: Node) -> None:
        take_mut(node)
        self.started = True


def main() -> None:
    n = Node()
    h = Holder(n)
    print(n.x)
    print(h.started)


main()
