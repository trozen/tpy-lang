# How an owning return takes a stored source: a consuming method moves its
# receiver out, a copied source is spelled with ITS type (never the slot's),
# a tuple element held by value is copied out (a missed move), a getter of a
# readonly Optional field builds.
from typing import Optional, Self

from tpy import Own, ValueType, readonly


class Name(ValueType):
    s: str

    def __init__(self, s: str) -> None:
        self.s = s

    def take(self: Own[Self]) -> Own[Self]:
        return self  # tpyc: ok

    def dup(self) -> Own["Name"]:
        return self  # tpyc: ok


class W:
    v: int

    def __init__(self, src: "W2") -> None:
        print("W.__init__ runs")
        self.v = src.v + 100


class W2(W):
    def __init__(self, v: int) -> None:  # tpyc: warning(/does not call 'super/)
        self.v = v


def live_sub() -> Own[W]:
    d = W2(3)

    def peek() -> int:
        return d.v

    r = peek()
    return d  # tpyc: warning(/upcast narrows/) warning(/copies W2/)


class P:
    v: int

    def __init__(self, v: int) -> None:
        self.v = v


def elem_of_local() -> Own[P]:
    t = (P(1), 2)
    # A copy where a move is possible: the tuple is never read again
    # (BUGS.md#owned-tuple-element-return-copies).
    return t[0]  # tpyc: warning(/copies P/)


def elem_of_param(t: Own[tuple[P, int]]) -> Own[P]:
    # Copied, not moved, although the tuple is owned; a `tuple[Own[P], P]`
    # param moves its element (BUGS.md#owned-tuple-element-return-copies).
    return t[0]  # tpyc: warning(/copies P/)


class Leaf:
    v: int

    def __init__(self, v: int) -> None:
        self.v = v


class Tree:
    child: Optional[Leaf]

    def __init__(self) -> None:
        self.child = Leaf(4)

    @property
    def kid(self) -> readonly[Optional[Leaf]]:
        return self.child  # tpyc: ok


def main() -> None:
    # consuming method: the receiver moves out whole.
    n = Name("hello world, long enough to heap allocate")
    m = n.take()
    print("consuming_self:", m.s)
    # non-consuming: a value record's result is a copy.
    d = m.dup()
    print("dup_self:", d.s, m.s)
    # a closure-read subclass local at an owning base slot: copied as W2.
    w = live_sub()
    w.v += 1
    print("live_subclass:", w.v)
    # a tuple element the tuple holds by value: copied out today.
    p = elem_of_local()
    p.v += 10
    print("tuple_elem_local:", p.v)
    q = elem_of_param((P(5), 6))
    q.v += 10
    print("tuple_elem_param:", q.v)
    # a getter's readonly Optional over a field: what this guards is the C++
    # build of both `const Leaf* kid()` overloads; a read of the getter does
    # not lower (BUGS.md#readonly-optional-getter-read-rejects), so the field
    # is read directly.
    tree = Tree()
    print("getter_opt:", tree.child.v if tree.child is not None else -1)

main()
