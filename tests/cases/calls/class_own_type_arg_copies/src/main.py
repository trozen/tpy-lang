# `C[Own[P]]` copies where its hand-written twin copies -- each copy warned,
# the members' at the line forming the class; `Box[P]` keeps aliasing.
from __future__ import annotations
from tpy import Own
from fixtures import Box, P
import part_restored
import part_generic
import part_lib
import part_members
import part_handover
import part_formations
import part_walk
import part_implicit
import part_pending_method


def core() -> None:
    loc = P(2)
    # a live local copied into the field
    b = Box[Own[P]](loc)  # tpyc: warning(/copies P into owned storage; use copy/) warning(/copies P into owned storage in 'Box.get' \(fixtures.py:25\)/) warning(/'Box.push_v' \(fixtures.py:32\)/) warning(/'Box.__iter__' \(fixtures.py:37\)/)
    loc.n = 20
    print("ctor", b.v.n, loc.n)
    # get hands back an owned copy: the box keeps its value
    g = b.get()
    g.n = 9
    print("get", b.v.n, g.n)
    q = P(3)
    b.push(q)  # tpyc: warning(/copies P into owned storage; use copy/)
    q.n = 30
    b.push(P(4))
    print("push", b.items[0].n, len(b.items))
    # each step is the iterator's own copy
    for p in b:
        p.n += 100
    print("iter", b.items[0].n, b.items[1].n)
    b.push_v()
    b.items[2].n = 77
    print("push_v", b.v.n, b.items[2].n)


def main() -> None:
    core()
    part_restored.main()
    part_generic.main()
    part_lib.main()
    part_members.main()
    part_handover.main()
    part_formations.main()
    part_walk.main()
    part_implicit.main()
    part_pending_method.main()


main()
