# A subclass object stored into base-class storage hands over its base part,
# copied or moved, as CPython keeps its value: `Base.__init__(Sub)` never runs.
from typing import Optional
from tpy import Own, int32


class Base:
    v: int32

    def __init__(self, s: "Sub") -> None:
        self.v = s.v * 10


class Sub(Base):
    def __init__(self, v: int32) -> None:  # tpyc: warning(/does not call 'super\(\).__init__/)
        self.v = v


# Each slot below holds its own copy (sema warns it), so nothing mutates the
# source after the store.
class H:
    b: Base
    o: Optional[Base]
    t: tuple[Base, int32]

    def __init__(self, s: Sub) -> None:
        # member-init: each slot copies the Sub as a Base
        self.b = s  # tpyc: warning(/upcast narrows/) warning(/copies Sub into field/)
        self.o = s  # tpyc: warning(/upcast narrows/) warning(/copies Sub into field/)
        self.t = (s, 1)  # tpyc: warning(/upcast narrows/) warning(/copies Base into field \(tuple element 0\)/)

    def put(self, s: Sub) -> None:
        # field writes: each slot copies the Sub as a Base
        self.b = s  # tpyc: warning(/upcast narrows/) warning(/copies Sub into field/)
        self.o = s  # tpyc: warning(/upcast narrows/) warning(/copies Sub into field/)
        self.t = (s, 2)  # tpyc: warning(/upcast narrows/) warning(/copies Base into field \(tuple element 0\)/)


class G:
    b: Base

    def __init__(self, s: Own[Sub]) -> None:
        # member-init: the owned Sub moves in as a Base
        self.b = s  # tpyc: warning(/upcast narrows/)

    def put_fresh(self) -> None:
        # field write: a fresh Sub moves in as a Base
        self.b = Sub(4)  # tpyc: warning(/upcast narrows/)


def widen(t: tuple[Sub, int32]) -> Own[tuple[Base, int32]]:
    # owning result: the lent Sub element copies into the Base element
    return t  # tpyc: warning(/upcast narrows/) warning(/copies Base into owned storage \(tuple element 0\)/)


def widen_opt(t: tuple[Sub, int32]) -> Own[tuple[Optional[Base], int32]]:
    # owning result: the Sub element copies into the Optional[Base] payload
    return t  # tpyc: warning(/upcast narrows/) warning(/copies Base \| None into owned storage \(tuple element 0\)/)


def widen_owned(t: Own[tuple[Sub, int32]]) -> Own[tuple[Base, int32]]:
    # owning result: the owned Sub element moves into the Base element
    return t  # tpyc: warning(/upcast narrows/)


def ret_opt_moved() -> Own[Base | None]:
    # owning result: the local Sub moves in at its implicit move
    s = Sub(5)
    return s  # tpyc: warning(/upcast narrows/)


def show(o: Optional[Base]) -> int32:
    return o.v if o is not None else -1


def main() -> None:
    s = Sub(7)
    h = H(s)
    print("init", h.b.v, show(h.o), h.t[0].v)
    s.v = 8
    h.put(s)
    print("put", h.b.v, show(h.o), h.t[0].v)
    w = widen((Sub(9), 0))
    print("widen", w[0].v)
    wo = widen_opt((Sub(6), 0))
    o, n = wo
    print("widen_opt", show(o), n)
    wn = widen_owned((Sub(3), 1))
    print("widen_owned", wn[0].v, wn[1])
    r = ret_opt_moved()
    print("ret_opt_moved", show(r))
    # container literal: the tuple element copies the Sub as a Base
    xs: list[tuple[int32, Base]] = [(1, s)]  # tpyc: warning(/upcast narrows/) warning(/copies Sub into owned storage \(tuple element 1\)/)
    print("list", xs[0][1].v)
    ys: list[tuple[int32, Optional[Base]]] = [(1, s)]  # tpyc: warning(/upcast narrows/) warning(/copies Sub into owned storage \(tuple element 1\)/)
    m, y = ys[0]
    print("list_opt", m, show(y), s.v)
    g = G(Sub(3))
    print("own_init", g.b.v)
    g.put_fresh()
    print("fresh_write", g.b.v)
    # container literal: a Sub at its last use moves in as a Base, and so
    # does a fresh one
    last = Sub(2)
    zs: list[tuple[int32, Base]] = [(1, last)]  # tpyc: warning(/upcast narrows/)
    print("list_moved", zs[0][1].v)
    fs: list[tuple[int32, Base]] = [(1, Sub(6))]  # tpyc: warning(/upcast narrows/)
    print("list_fresh", fs[0][1].v)


main()
