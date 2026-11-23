# A class at `Own[P]` forms every class it names at any depth -- behind a
# `Ptr` or a `Callable` field, at another instantiation of itself (rotated or
# permuted), in a recursive alias, in a generic signature -- reporting their
# members' copies where it is formed; at `P` the same members lend.
from __future__ import annotations
from typing import Callable
from tpy import Own, Ptr, int32
from fixtures import Box, P


class ByPtr[T]:
    p: Ptr[Box[T]]

    def __init__(self) -> None:
        self.p = Ptr()


class ByFn[T]:
    f: Callable[[], Own[Box[T]]]

    def __init__(self, f: Callable[[], Own[Box[T]]]) -> None:
        self.f = f


class Node[T]:
    v: T
    kids: list[Node[Own[T]]]

    def __init__(self, v: T) -> None:
        self.v = v  # tpyc: warning(/may copy T into field/)
        self.kids = []

    def get(self) -> T:
        return self.v


class Rot[A, B, C, D, E]:
    v: A
    kids: list[Rot[B, C, D, E, A]]

    def __init__(self, v: A) -> None:
        self.v = v  # tpyc: warning(/may copy A into field/)
        self.kids = []

    def get(self) -> A:
        return self.v


# two fields permuting six arguments: finitely many instantiations, but more
# than a nested walk could hold
class Perm[A, B, C, D, E, F]:
    v: A
    # a `Ptr` field: BUGS.md#finite-class-field-cycle-recursion-crash
    p: Ptr[int32]
    left: list[Perm[B, C, D, E, F, A]]
    right: list[Perm[B, A, C, D, E, F]]

    def get(self) -> A:
        return self.v


type Tree[T] = Box[T] | list[Tree[T]]


class Gen[T]:
    v: T
    items: list[T]

    def __init__(self, v: T) -> None:
        self.v = v  # tpyc: warning(/may copy T into field/)
        self.items = []

    def one(self, x: T) -> int32:
        self.items.append(x)  # tpyc: warning(/may copy T into owned storage/)
        return 1

    def via_u[U](self, u: U) -> int32:
        # the frame's copy is the class's: reported once, where it is formed
        return sum(self.one(self.v) for _ in range(2))


def peek[T](p: Ptr[Box[T]]) -> int32:
    return 1


def mkbox() -> Own[Box[Own[P]]]:  # tpyc: warning(/'Box.get'/) warning(/'Box.push_v'/) warning(/'Box.__iter__'/)
    return Box[Own[P]](P(2))  # tpyc: warning(/'Box.get'/) warning(/'Box.push_v'/) warning(/'Box.__iter__'/)


def by_ptr(o: ByPtr[Own[P]]) -> int32:  # tpyc: warning(/copies P into owned storage in 'Box.get' \(fixtures.py:25\)/)
    return 1


def by_fn(o: ByFn[Own[P]]) -> None:  # tpyc: warning(/copies P into owned storage in 'Box.get' \(fixtures.py:25\)/)
    b = o.f()
    g = b.get()
    g.n = 20
    print("walk-fn", b.v.n, g.n)


# the alias's body names `Box[Own[P]]` once its instance is minted
def by_alias(t: Tree[Own[P]]) -> int32:  # tpyc: warning(/copies P into owned storage in 'Box.get' \(fixtures.py:25\)/)
    return 1


# a field rotating the arguments reaches `Rot[Own[P], ...]` five levels down
def by_rotation(o: Rot[int32, int32, int32, int32, Own[P]]) -> int32:  # tpyc: warning(/copies P into owned storage in 'Rot.get' \(part_walk.py:46\)/)
    return 1


def by_rotation_plain(o: Rot[int32, int32, int32, int32, P]) -> int32:  # tpyc: ok
    return 2


def by_permutation(o: Perm[int32, int32, int32, int32, int32, Own[P]]) -> int32:  # tpyc: warning(/copies P into owned storage in 'Perm.get' \(part_walk.py:59\)/)
    return 1


def by_permutation_plain(o: Perm[int32, int32, int32, int32, int32, P]) -> int32:  # tpyc: ok
    return 2


def recursive() -> None:
    # `Node[P]` holds `Node[Own[P]]` children, whose getter copies
    n = Node[P](P(3))  # tpyc: warning(/copies P into owned storage in 'Node.get' \(part_walk.py:34\)/)
    n.kids.append(Node[Own[P]](P(4)))  # tpyc: warning(/'Node.get'/)
    k = n.kids[0].get()
    k.n = 40
    print("walk-node", n.kids[0].v.n, k.n)
    # the parent's getter at `P` lends
    m = n.get()
    m.n = 30
    print("walk-node-plain", n.v.n)


def signature() -> None:
    # `Box[Own[P]]` named only by `peek`'s parameter at its instantiation
    print("walk-sig", peek[Own[P]](Ptr()))  # tpyc: warning(/copies P into owned storage in 'Box.get' \(fixtures.py:25\)/)
    print("walk-sig-plain", peek[P](Ptr()))  # tpyc: ok


def genexpr_u() -> None:
    k = Gen[Own[P]](P(5))  # tpyc: warning(/copies P into owned storage in 'Gen.via_u' \(part_walk.py:79\)/)
    print("walk-genexpr", k.via_u(1))  # tpyc: ok


def main() -> None:
    print("walk-ptr", by_ptr(ByPtr[Own[P]]()))  # tpyc: warning(/'Box.get'/) warning(/'Box.push_v'/) warning(/'Box.__iter__'/)
    by_fn(ByFn[Own[P]](mkbox))  # tpyc: warning(/'Box.get'/) warning(/'Box.push_v'/) warning(/'Box.__iter__'/)
    print("walk-rot", by_rotation(Rot[int32, int32, int32, int32, Own[P]](1)),  # tpyc: warning(/'Rot.get'/)
          by_rotation_plain(Rot[int32, int32, int32, int32, P](2)))  # tpyc: ok
    recursive()
    signature()
    genexpr_u()
