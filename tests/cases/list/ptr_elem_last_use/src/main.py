# A record stored into a Ptr slot at its last use: the slot takes the
# record's address (`&a`), so nothing is moved out of it. Likewise what
# lands in a union's view member: the view refers to its buffer.
import asyncio
from typing import Iterator
from tpy import BytesView, Own, Ptr, Span, StrView, int32, nocopy


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


@nocopy
class NC:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


# free function: append, insert and setdefault at the last use
def free_sinks() -> None:
    ps: list[Ptr[P]] = []
    a = P(1)
    ps.append(a)  # tpyc: ok
    b = P(2)
    ps.insert(0, b)  # tpyc: ok
    d: dict[str, Ptr[P]] = {}
    c = P(3)
    d.setdefault("k", c)  # tpyc: ok
    print("free", ps[0].n, ps[1].n, d["k"].n)


# constructor: a field and a local list fed from an owned local, read
# before the constructor returns (the field outlives `a`:
# BUGS.md#ptr-store-of-body-local-outlives-body)
class Holder:
    p: Ptr[P]

    def __init__(self, seed: int32) -> None:
        a = P(seed)
        self.p = a  # tpyc: ok
        print("ctor field", self.p.n)
        ps: list[Ptr[P]] = []
        b = P(seed + 1)
        ps.append(b)  # tpyc: ok
        print("ctor append", ps[0].n)

    # method: the same append off a method body
    def fill(self, n: int32) -> None:
        ps: list[Ptr[P]] = []
        a = P(n)
        ps.append(a)  # tpyc: ok
        print("method", ps[0].n)


# loop element: the element's address, the source list keeps it alive
def loop_elem() -> None:
    xs = [P(7), P(8)]
    ps: list[Ptr[P]] = []
    for a in xs:
        ps.append(a)  # tpyc: ok
    print("loop", ps[0].n, ps[1].n)


# generator: a frame local appended, read while the frame lives
def gen() -> Iterator[int32]:
    ps: list[Ptr[P]] = []
    a = P(9)
    ps.append(a)  # tpyc: ok
    yield ps[0].n


# async: the same in a coroutine frame
async def co() -> int32:
    ps: list[Ptr[P]] = []
    a = P(10)
    ps.append(a)  # tpyc: ok
    await asyncio.sleep(0)
    return ps[0].n


# aliasing: both elements point at `a`; a write through one shows in the other
def aliasing() -> None:
    ps: list[Ptr[P]] = []
    a = P(11)
    ps.append(a)
    ps.append(a)  # tpyc: ok
    ps[0].n = 12
    print("alias", ps[1].n)


# inverse: a later use of the record keeps rendering `&a`
def later_use() -> None:
    ps: list[Ptr[P]] = []
    a = P(13)
    ps.append(a)  # tpyc: ok
    a.n = 14
    print("later", ps[0].n, a.n)


# inverse: views of a last-use buffer, unchanged
def views(k: int32) -> None:
    vs: list[StrView] = []
    s = "ab" + str(k)
    vs.append(s)  # tpyc: ok
    for v in vs:
        print("strview", v)
    sp: list[Span[int32]] = []
    xs = [1, 2, k]
    sp.append(xs)  # tpyc: ok
    print("span", sp[0][2])
    bv: list[BytesView] = []
    bs = b"xy" + bytes([k])
    bv.append(bs)  # tpyc: ok
    for w in bv:
        print("bytesview", len(w))


# a str at its last use lands in the view member of a union: the view
# refers to it, so nothing is moved (`u = s`)
def union_view(k: int32) -> None:
    s = "ab" + str(k)
    u: int32 | StrView = s  # tpyc: ok
    print("union_view", u)


# a @nocopy record appended by address and used later: nothing is moved
def nocopy_later() -> None:
    ns: list[Ptr[NC]] = []
    c = NC(15)
    ns.append(c)  # tpyc: ok
    print("nocopy", c.n, ns[0].n)


# an Own parameter only stored by address is never consumed
def own_param_addr(a: Own[P]) -> None:  # tpyc: warning(/never consumed/)
    ps: list[Ptr[P]] = []
    ps.append(a)
    print("own_param", ps[0].n)


# constructor: an Own parameter stored straight into a Ptr field keeps its
# address (`p(&a)`), so it is never consumed; the field outlives the
# by-value parameter (BUGS.md#ptr-store-of-body-local-outlives-body)
class HPtr:
    p: Ptr[P]

    def __init__(self, a: Own[P]) -> None:  # tpyc: warning(/never consumed/)
        self.p = a
        print("own_field", self.p.n)


class Base:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Der(Base):
    def __init__(self, n: int32) -> None:
        super().__init__(n)


# a subclass into a union of its base and a view lands in the base member:
# the Own parameter is consumed (moved into the field), no warning
class HU:
    u: Base | StrView

    def __init__(self, d: Own[Der]) -> None:  # tpyc: ok
        self.u = d  # tpyc: warning(/upcast narrows/)


def union_base() -> None:
    d = Der(17)
    h = HU(d)
    print("union_base", isinstance(h.u, Base))


def main() -> None:
    free_sinks()
    h = Holder(5)
    h.fill(6)
    loop_elem()
    for v in gen():
        print("gen", v)
    print("async", asyncio.run(co()))
    aliasing()
    later_use()
    views(7)
    union_view(8)
    nocopy_later()
    own_param_addr(P(16))
    HPtr(P(18))
    union_base()


main()
