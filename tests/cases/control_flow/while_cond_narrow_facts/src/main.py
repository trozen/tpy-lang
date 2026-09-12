# A `while` whose COMPOUND condition sema still narrowed: the head renders the
# plain test, and the body carries the loop-entry extraction alias, so reads of
# the subject inside the body see the narrowed member.
from tpy import int32, ValueType


class A(ValueType):
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class B(ValueType):
    m: int32

    def __init__(self, m: int32) -> None:
        self.m = m


class Node:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class Leaf:
    w: int32

    def __init__(self, w: int32) -> None:
        self.w = w


def and_not(u: A | B, flag: bool) -> int32:
    t = 0
    # Negated isinstance under `and`: the body knows u is A.
    while not isinstance(u, B) and flag:
        t += u.n
        flag = False
    return t


def not_or(u: A | B, flag: bool) -> int32:
    t = 0
    # A negated OR chain leaves the same single fact: the body knows u is B.
    while not (isinstance(u, A) or flag):
        t += u.m
        flag = True
    return t


def ref_union(u: Node | Leaf, flag: bool) -> int32:
    # The reference-union twin: the alias borrows the pointer variant, so the
    # mutation inside the loop is visible on the caller`s object afterwards.
    while not isinstance(u, Leaf) and flag:
        u.v += 5
        flag = False
    if isinstance(u, Node):
        return u.v
    return -1


def main() -> None:
    print(and_not(A(3), True))
    print(and_not(B(4), True))
    print(not_or(B(7), False))
    n = Node(1)
    print(ref_union(n, True))
    print(n.v)


main()
