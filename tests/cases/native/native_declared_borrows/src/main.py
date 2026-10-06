# `borrows=(...)` on a native function's or method's `@native` says its
# result IS one of the named arguments (`self` naming a method's receiver):
# the call binds as a borrow (`Node& n`) and a write through it reaches that
# argument; `element_of=(...)` says it is an ELEMENT of one, which a
# container argument lends. Without the declaration the result is a fresh
# value: binding it copies.
import asyncio
from typing import Iterator
from tpy import int32, Own, NativeIterable, pure, readonly
from tpy.extern import native


@native
class Node:
    v: int32

    @native(borrows=("other",))
    @readonly
    def pick(self, other: Node) -> Node: ...


@native("::Bag", elements=True)
class Bag[T](NativeIterable[T]):
    def __init__(self) -> None: ...

    @native("tpy::__iter__", function=True)
    @pure
    @readonly
    def __iter__(self) -> Iterator[T]: ...

    @native("push")
    def push(self, v: Own[T]) -> None: ...

    @native("first", element_of=("self",))
    @readonly
    def first(self) -> T: ...


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


# method, borrows= an argument: a borrow of that argument
def method_arg() -> None:
    a = Node(1)
    b = Node(2)
    n = a.pick(b)  # tpyc: ok
    n.v = 50
    print("method_arg", a.v, b.v)


# method on a readonly receiver lending a mutable argument: the receiver
# lends nothing, so the result stays writable
def method_ro_receiver(ro: readonly[Node], x: Node) -> None:
    n = ro.pick(x)  # tpyc: ok
    n.v = 60


# ... and the same held in a generator / coroutine frame and hoisted across
# if/else: the frame slot and the hoisted pointer stay writable too
def method_ro_gen(ro: readonly[Node], x: Node) -> Iterator[int32]:
    n = ro.pick(x)  # tpyc: ok
    yield 1
    n.v = 61
    yield n.v


async def method_ro_co(ro: readonly[Node], x: Node) -> int32:
    n = ro.pick(x)  # tpyc: ok
    n.v = 62
    return n.v


def method_ro_branch(ro: readonly[Node], x: Node, y: Node, c: bool) -> None:
    if c:
        n = ro.pick(x)  # tpyc: ok
    else:
        n = ro.pick(y)  # tpyc: ok
    n.v = 63


# method, element_of= the receiver: a borrow of the container's element
def method_elem() -> None:
    b = Bag[Node]()
    b.push(Node(7))
    e = b.first()  # tpyc: ok
    e.v = 70
    for n in b:
        print("method_elem", n.v)


def main() -> None:
    declared()
    element()
    undeclared()
    method_arg()
    a = Node(1)
    x = Node(2)
    method_ro_receiver(a, x)
    print("method_ro_receiver", a.v, x.v)
    for s in method_ro_gen(a, x):
        print("method_ro_gen", s, x.v)
    print("method_ro_co", asyncio.run(method_ro_co(a, x)), x.v)
    y = Node(3)
    method_ro_branch(a, x, y, False)
    print("method_ro_branch", x.v, y.v)
    method_elem()


main()
