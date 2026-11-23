# A field `v: T` read at `Own[...]` holds its storage form, as the twin's
# `v: P`, at every reader: an Optional field through it, a comprehension and
# a loop over it, an inherited field, a match class pattern.
from __future__ import annotations
from tpy import Own, int32
from fixtures import Box


class Q:
    n: int32
    opt: int32 | None

    def __init__(self, n: int32) -> None:
        self.n = n
        self.opt = n


class Base[T]:
    v: T

    def __init__(self, v: T) -> None:
        self.v = v  # tpyc: warning(/may copy T into field/)


class Kid(Base[Own[Q]]):  # tpyc: ok
    def bump(self) -> None:
        # the inherited field holds a `Q`
        self.v.n += 1


def matched(u: Box[Own[Q]] | int32) -> int32:  # tpyc: warning(/'Box.get'/) warning(/'Box.push_v'/) warning(/'Box.__iter__'/)
    match u:
        # the field's pattern matches its storage form `Q`
        case Box(v=Q(n=k)):
            return k
        case _:
            return 0


def main() -> None:
    b = Box[Own[Q]](Q(5))  # tpyc: warning(/'Box.get'/) warning(/'Box.push_v'/) warning(/'Box.__iter__'/)
    if b.v.opt is not None:
        print("opt", b.v.opt)
    bl = Box[Own[list[int32]]]([1, 2])  # tpyc: warning(/copies list\[int32\] into owned storage in 'Box.get'/) warning(/'Box.push_v'/) warning(/'Box.__iter__'/)
    total = 0
    for x in bl.v:
        total += x
    print("comp", [x + 1 for x in bl.v], total)
    k = Kid(Q(3))
    k.bump()
    print("kid", k.v.n)
    print("match", matched(b))
