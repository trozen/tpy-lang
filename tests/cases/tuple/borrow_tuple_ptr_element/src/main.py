# A Ptr[T] borrow-tuple element must pass BARE (it already is the pointer), from
# a local, a param and a list subscript; the plain-record local is the inverse.
from tpy import Ptr, int32


class Node:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


def bump(t: tuple[Node, int32]) -> None:
    t[0].x = t[0].x + t[1]


def via_local(items: list[Node]) -> int32:
    p: Ptr[Node] = items[0]
    bump((p, 10))
    return items[0].x


def via_param(p: Ptr[Node]) -> None:
    bump((p, 100))


def via_subscript(ps: list[Ptr[Node]]) -> None:
    bump((ps[0], 1000))


def via_plain() -> int32:
    n = Node(5)
    bump((n, 10000))
    return n.x


def main() -> None:
    items = [Node(1), Node(2)]
    print(via_local(items))
    via_param(items[0])
    print(items[0].x)
    ps: list[Ptr[Node]] = []
    ps.append(items[0])
    via_subscript(ps)
    print(items[0].x)
    print(via_plain())


main()
