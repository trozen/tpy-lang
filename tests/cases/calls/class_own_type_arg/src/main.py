# `C[Own[P]]` is the class with `Own[P]` substituted for `T`, as its
# hand-written twin; these sections copy nothing, so CPython agrees.
from __future__ import annotations
from tpy import Own, int32
from fixtures import Bag, Box, N, P
import part_members
import part_generic
import part_lib
import part_scopes
import part_fields
import part_base
import part_handover
import part_binder
import part_pending


# a module-level global forms the class: each member's copy is reported here
G = Box[Own[P]](P(7))  # tpyc: warning(/copies P into owned storage in 'Box.get' \(fixtures.py:33\)/) warning(/'Box.push_v' \(fixtures.py:40\)/) warning(/'Box.__iter__' \(fixtures.py:45\)/)


# an annotation alone forms the class too
def count(b: Box[Own[P]]) -> int32:  # tpyc: warning(/copies P into owned storage in 'Box.get'/) warning(/'Box.push_v'/) warning(/'Box.__iter__'/)
    return len(b.items)


# a subclass of `Box[Own[P]]` instantiates its base there
class Sub(Box[Own[P]]):  # tpyc: warning(/copies P into owned storage in 'Box.get'/) warning(/'Box.push_v'/) warning(/'Box.__iter__'/)
    def bump(self) -> None:
        self.v.n += 1


def core() -> None:
    # a fresh value and a local at its last use move in
    b = Box[Own[P]](P(2))  # tpyc: warning(/'Box.get'/) warning(/'Box.push_v'/) warning(/'Box.__iter__'/)
    q = P(3)
    b.push(q)  # tpyc: ok
    b.push(P(5))  # tpyc: ok
    b.items[0].n = 30
    print("core", b.v.n, b.items[0].n, len(b.items), count(b))


def plain() -> None:
    # `Box[P]`'s `get` lends, as CPython's
    b = Box[P](P(2))
    g = b.get()
    g.n = 9
    print("plain", b.v.n)


def nocopy_payload() -> None:
    # `Bag[Own[N]]` holds a non-copyable payload: every store moves
    b = Bag[Own[N]](N(4))  # tpyc: ok
    b.push(N(5))  # tpyc: ok
    print("nocopy", b.v.n, b.items[0].n)


def subclass() -> None:
    sub = Sub(P(3))
    sub.bump()
    sub.v.n += 10
    print("sub", sub.v.n)


def main() -> None:
    core()
    plain()
    nocopy_payload()
    subclass()
    G.push(P(8))
    G.items[0].n = 80
    print("global", G.v.n, G.items[0].n)
    part_members.main()
    part_generic.main()
    part_lib.main()
    part_scopes.main()
    part_fields.main()
    part_base.main()
    part_handover.main()
    part_binder.main()
    part_pending.main()


main()
