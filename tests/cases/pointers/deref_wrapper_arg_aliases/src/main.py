# A record wrapper's `__deref__()` passed at a plain record parameter binds the
# wrapped object itself, never a copy: every section writes through the
# parameter and prints the object the wrapper holds.
import asyncio
from typing import Iterator
from tpy import Ptr, int32, nocopy, readonly
from tplib.box import Box


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class R:
    _p: P

    def __init__(self) -> None:
        self._p = P(0)

    def __deref__(self) -> P:
        return self._p

    def bump_self(self) -> None:
        bump(self)  # tpyc: ok


@nocopy
class Q:
    n: int32

    def __init__(self) -> None:
        self.n = 0


class RQ:
    _q: Q

    def __init__(self) -> None:
        self._q = Q()

    def __deref__(self) -> Q:
        return self._q


class RO:
    _p: P

    def __init__(self) -> None:
        self._p = P(3)

    @readonly
    def __deref__(self) -> readonly[P]:
        return self._p


class H:
    r: R
    opt: R | None

    def __init__(self) -> None:
        self.r = R()
        self.opt = R()

    @property
    def r_prop(self) -> R:
        return self.r

    def bump_mine(self) -> None:
        bump(self.r)  # tpyc: ok


def bump(p: P) -> None:
    p.n += 1


def bump_n(p: P) -> int32:
    p.n += 1
    return p.n


def bump_q(q: Q) -> None:
    q.n += 1


def show_ro(p: readonly[P]) -> int32:
    return p.n


def n_of(r: R) -> int32:
    return r._p.n


def via_param(r: R) -> None:
    bump(r)  # tpyc: ok


def via_optional(r: R | None) -> None:
    if r is not None:
        bump(r)  # tpyc: ok


def via_ro(r: readonly[R]) -> int32:
    return show_ro(r)  # tpyc: ok


def via_tuple(t: tuple[R, int32]) -> None:
    bump(t[0])  # tpyc: ok


def gen(r: R) -> Iterator[int32]:
    bump(r)  # tpyc: ok
    yield n_of(r)


def gen_frame(p: P) -> Iterator[int32]:
    p.n += 1
    yield p.n


async def coro(r: R) -> int32:
    bump(r)  # tpyc: ok
    await asyncio.sleep(0)
    return n_of(r)


def main() -> None:
    # local wrapper
    r = R()
    bump(r)  # tpyc: ok
    print("local", n_of(r))
    # field of a local, a field of self in a method, a property
    h = H()
    bump(h.r)  # tpyc: ok
    h.bump_mine()
    bump(h.r_prop)  # tpyc: ok
    print("field", n_of(h.r))
    # narrowed Optional field
    if h.opt is not None:
        bump(h.opt)  # tpyc: ok
        print("optfield", n_of(h.opt))
    # list element
    rs = [R(), R()]
    bump(rs[1])  # tpyc: ok
    print("element", n_of(rs[1]))
    # parameter, narrowed Optional parameter, tuple element, `self`
    via_param(r)
    via_optional(r)
    via_tuple((r, 0))
    r.bump_self()
    print("param", n_of(r))
    # a reseated local (a pointer slot)
    a = R()
    b = R()
    x = a
    if n_of(r) > 0:
        x = b
    bump(x)  # tpyc: ok
    print("reseat", n_of(a), n_of(b))
    # a readonly wrapper at a readonly slot reads the const overload, and a
    # `-> readonly[P]` deref target binds a readonly slot
    print("readonly", via_ro(r))
    ro = RO()
    print("readonly target", show_ro(ro))  # tpyc: ok
    # for-loop variable and comprehension variable
    for e in rs:
        bump(e)  # tpyc: ok
    print("loop", n_of(rs[0]), n_of(rs[1]))
    ns = [bump_n(e) for e in rs]  # tpyc: ok
    print("comp", ns)
    # a `Ptr` loop variable writes only its pointee
    objs = [P(0)]
    ps: list[Ptr[P]] = []
    ps.append(objs[0])
    for p in ps:
        bump(p)  # tpyc: ok
    print("ptr loop", objs[0].n)
    # generator body, and a generator frame that keeps the parameter
    for v in gen(r):
        print("gen", v)
    for v in gen_frame(r):  # tpyc: ok
        print("frame", v, n_of(r))
    # async body
    print("async", asyncio.run(coro(r)))

    # closure body
    def inner() -> None:
        bump(r)  # tpyc: ok

    inner()
    print("closure", n_of(r))
    # try/finally body
    try:
        bump(r)  # tpyc: ok
    finally:
        print("finally", n_of(r))
    # match arm
    match n_of(r):
        case 10:
            bump(r)  # tpyc: ok
        case _:
            pass
    print("match", n_of(r))
    # a @nocopy target, which a copy could not even build
    rq = RQ()
    bump_q(rq)  # tpyc: ok
    print("nocopy", rq._q.n)
    # the library Box
    bx = Box(P(0))
    bump(bx)  # tpyc: ok
    print("box", bx.get().n)


main()
# module level: the wrapper is a global pointer slot
g = R()
bump(g)  # tpyc: ok
gs = [R()]
for ge in gs:
    bump(ge)  # tpyc: ok
print("module", n_of(g), n_of(gs[0]))
