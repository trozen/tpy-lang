# `borrows=(...)` on a native free function's `@native` says its result IS
# one of the named arguments: the call binds as a borrow (`Node& n`) and a
# write through it reaches that argument; `element_of=(...)` says it is an
# ELEMENT of one, which a container argument lends. Without the declaration
# the result is a fresh value: binding it copies.
from tpy import int32
from tpy.extern import native


@native
class Node:
    v: int32


@native(borrows=("a", "b"))
def pick_ref(a: Node, b: Node) -> Node: ...


@native(element_of=("items",))
def first(items: list[Node]) -> Node: ...


# The same C++ shape, undeclared: the result is copied into the local.
@native
def pick_copy(a: Node, b: Node) -> Node: ...


# free function, declared: a borrow of the argument it returns
def declared() -> None:
    a = Node(1)
    b = Node(2)
    n = pick_ref(a, b)
    n.v = 10
    print("declared", a.v, b.v)


# element_of: a borrow of the container's element
def element() -> None:
    ns = [Node(1), Node(2)]
    f = first(ns)
    f.v = 30
    print("element", ns[0].v)


# free function, undeclared: an independent copy
def undeclared() -> None:
    a = Node(1)
    b = Node(2)
    c = pick_copy(a, b)
    c.v = 20
    print("undeclared", a.v, b.v, c.v)


def main() -> None:
    declared()
    element()
    undeclared()


main()
