# An `if` whose COMPOUND condition sema still narrowed: the head renders the
# plain test, and the branch that the fact belongs to carries the extraction
# alias, so reads of the subject inside it see the narrowed member.
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


def then_side(u: A | B, flag: bool) -> int32:
    # A negated OR chain: the THEN branch knows u is B.
    if not (isinstance(u, A) or flag):
        return u.m
    return 0


def else_side(u: A | B, flag: bool) -> int32:
    # The complement lands on the ELSE branch, which knows u is A.
    if not isinstance(u, A) or flag:
        return 5
    else:
        return u.n


def unread_subject(u: A | B, flag: bool) -> int32:
    # The narrowed branch never reads the subject, so its alias goes unused --
    # the build must stay warning-clean.
    if not isinstance(u, A) or flag:
        return 0
    else:
        return 1


def ref_union(u: Node | Leaf, flag: bool) -> int32:
    # The reference-union twin: the alias borrows the pointer variant, so the
    # mutation is visible on the caller`s object afterwards.
    if not isinstance(u, Node) or flag:
        return -1
    else:
        u.v += 5
        return u.v


def main() -> None:
    print(then_side(B(3), False))
    print(then_side(A(3), False))
    print(else_side(A(4), False))
    print(else_side(A(4), True))
    print(unread_subject(A(1), False))
    n = Node(1)
    print(ref_union(n, False))
    print(n.v)


main()
