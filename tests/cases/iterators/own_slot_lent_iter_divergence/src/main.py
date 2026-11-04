# A user iterator lending its step from storage the call does not name copies
# each element into an owning element slot: warned; CPython aliases instead.
import asyncio
from typing import Iterable, Iterator
from tpy import Own, Ptr, SpanIter, int32, copy, nocopy
from tplib.array_list import ArrayList

G = [[1], [2]]


def reset() -> None:
    G.clear()
    G.append([1])
    G.append([2])


class View:
    def __iter__(self) -> Iterator[list[int32]]:
        for x in G:
            yield x


class Child(View):
    pass


class GV[T]:
    def __iter__(self) -> Iterator[list[int32]]:
        for x in G:
            yield x


def passthru[T](src: Iterable[T]) -> Iterator[T]:
    for x in src:
        yield x


def gen() -> Iterator[list[int32]]:
    for x in G:
        yield x


def nth(i: int32) -> Iterator[list[int32]]:
    yield G[i]


def gen_l(xs: list[int32]) -> Iterator[list[int32]]:
    for x in G:
        yield x


def gen_gt[T](t: T) -> Iterator[list[int32]]:
    for x in G:
        yield x


class Cursor:
    i: int32

    def __init__(self) -> None:
        self.i = 0

    def __iter__(self) -> "Cursor":
        return self

    def __next__(self) -> list[int32]:
        if self.i >= len(G):
            raise StopIteration
        self.i += 1
        return G[self.i - 1]


class It:
    i: int32

    def __init__(self) -> None:
        self.i = 0

    def __next__(self) -> list[int32]:
        if self.i >= len(G):
            raise StopIteration
        self.i += 1
        return G[self.i - 1]


class Separate:
    def __iter__(self) -> Own[It]:
        return It()


class Rows:
    items: list[list[int32]]

    def __init__(self) -> None:
        self.items = [[1], [2]]


class PView:
    rows: Ptr[Rows]

    def __init__(self, rows: Ptr[Rows]) -> None:
        self.rows = rows

    def __iter__(self) -> Iterator[list[int32]]:
        yield self.rows.items[0]
        yield self.rows.items[1]


def view_of(rows: Ptr[Rows]) -> Own[PView]:
    return PView(rows)


class Own2:
    items: list[list[int32]]

    def __init__(self) -> None:
        self.items = [[1], [2]]

    def __iter__(self) -> Iterator[list[int32]]:
        for x in self.items:
            yield x


@nocopy
class Tag:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class Pt:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


GP = [Pt(1)]
GA = ArrayList[Pt, 4]()


class PtView:
    def __iter__(self) -> Iterator[Pt]:
        for p in GP:
            yield p


class SpanView:
    def __iter__(self) -> SpanIter[Pt]:
        return GA.__iter__()


class Key:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v

    def __eq__(self, other: "Key") -> bool:
        return other.v == self.v

    def __hash__(self) -> int:
        return hash(self.v)


GK = [Key(1)]


class KeyView:
    def __iter__(self) -> Iterator[Key]:
        for k in GK:
            yield k


class PList(list[Pt]):
    pass


def bump(source: Iterable[Own[list[int32]]]) -> None:
    for x in source:
        x.append(99)


def bump_pt(source: Iterable[Own[Pt]]) -> None:
    for p in source:
        p.v += 100


def bump_pairs(source: Iterable[Own[tuple[int32, list[int32]]]]) -> None:
    for p in source:
        print("  pair", p[0])


def take(i: int32, x: Own[list[int32]]) -> Own[list[int32]]:
    x.append(i)
    return x


def keep(x: list[int32]) -> bool:
    return True


def free_function(c: bool) -> None:
    # free function: every lending source the owning slot copies from
    reset()
    bump(View())  # tpyc: warning(/copies list\[int32\] elements; use copy_iter/)
    print("free record", G)
    bump(gen())  # tpyc: warning(/copies list\[int32\] elements/)
    print("free generator", G)
    bump(nth(0))  # tpyc: warning(/copies list\[int32\] elements/)
    print("free generator(arg)", G)
    bump(gen_l([5]))  # tpyc: warning(/copies list\[int32\] elements/)
    print("free generator(list arg)", G)
    bump(gen_gt(1))  # tpyc: warning(/copies list\[int32\] elements/)
    print("free generic generator, closed step", G)
    gl = list(gen_gt(1))  # tpyc: warning(/copies list\[int32\] elements/)
    gl[0].append(7)
    print("free generic generator list()", G)
    bump(passthru(View()))  # tpyc: warning(/copies list\[int32\] elements/)
    print("free pass-through generic", G)
    pt = list(passthru(View()))  # tpyc: warning(/copies list\[int32\] elements/)
    pt[0].append(7)
    print("free pass-through generic list()", G)
    # a generic generator declared `Iterator[T]` lends (a copying body is an
    # error there), so a named argument's elements copy at the owning slot
    cg: list[list[int32]] = [[15]]
    bump(passthru(cg))  # tpyc: warning(/copies list\[int32\] elements/)
    print("free copying generic", cg)
    bump(Cursor())  # tpyc: warning(/copies list\[int32\] elements/)
    print("free next", G)
    bump(Separate())  # tpyc: warning(/copies list\[int32\] elements/)
    print("free separate iterator", G)
    bump(Child())  # tpyc: warning(/copies list\[int32\] elements/)
    print("free inherited", G)
    a = list(View())  # tpyc: warning(/copies list\[int32\] elements/)
    a[0].append(7)
    print("free list()", G)
    ks = set(KeyView())  # tpyc: warning(/copies Key elements/)
    # a write to the source after the call does not reach the set's copy
    # (the set's own element is const: BUGS.md#set-element-const-unmodelled)
    GK[0].v += 5
    for k in ks:
        print("free set()", k.v, GK[0].v)
    m = list(map(take, [5, 6], View()))  # tpyc: warning(/copies list\[int32\] elements/)
    print("free map", G, len(m))
    ns = [1, 2]
    z = list(zip(ns, View()))  # tpyc: warning(/copies tuple\[int32, list\[int32\]\] elements/)
    z[0][1].append(7)
    print("free zip", G)
    bump(filter(keep, View()))  # tpyc: warning(/copies list\[int32\] elements/)
    print("free filter", G)
    bump_pairs(enumerate(View()))  # tpyc: warning(/copies tuple\[int32, list\[int32\]\] elements/)
    bump(iter(View()))  # tpyc: warning(/copies list\[int32\] elements/)
    print("free iter", G)
    v = View()
    bump(v)  # tpyc: warning(/copies list\[int32\] elements/)
    print("free dead local", G)
    lv = View()
    bump(lv)  # tpyc: warning(/copies list\[int32\] elements/)
    for x in lv:
        x.append(1)
    print("free live local", G)
    if c:
        w = View()
    else:
        w = View()
    bump(w)  # tpyc: warning(/copies list\[int32\] elements/)
    print("free branch-bound", G)
    r = View()
    r = View()
    bump(r)  # tpyc: warning(/copies list\[int32\] elements/)
    print("free reassigned", G)
    ie = View() if c else View()
    bump(ie)  # tpyc: warning(/copies list\[int32\] elements/)
    print("free if-expression", G)
    rows = Rows()
    bump(view_of(rows))  # tpyc: warning(/copies list\[int32\] elements/)
    print("free Own factory", rows.items)
    cv = View()
    bump(copy(cv))  # tpyc: warning(/copies list\[int32\] elements/)
    print("free copy()", G)
    bump(GV[Own[list[int32]]]())  # tpyc: warning(/copies list\[int32\] elements/)
    print("free Own type arg", G)
    # a record iterating its own field, temporary or dead: the copy is real
    # (the step is lent) though nothing can observe it
    bump(Own2())  # tpyc: warning(/copies list\[int32\] elements/)
    o = Own2()
    bump(o)  # tpyc: warning(/copies list\[int32\] elements/)
    print("free own field")
    GA.append(Pt(1))
    bump_pt(SpanView())  # tpyc: warning(/copies Pt elements/)
    print("free SpanIter", GA[0].v)
    # a @nocopy zip half moves; only the lent half copies, either side
    zt = list(zip([Tag(7)], PtView()))  # tpyc: warning(/copies tuple\[Tag, Pt\] elements/)
    zt[0][1].v += 100
    print("free zip nocopy half", zt[0][0].v, GP[0].v)
    zm = list(zip(PtView(), [Tag(8)]))  # tpyc: warning(/copies tuple\[Pt, Tag\] elements/)
    zm[0][0].v += 100
    print("free zip nocopy half mirrored", zm[0][1].v, GP[0].v)
    q = Pt(1)
    pl = PList()
    # q is read below, so the append rightly copies it (warned)
    pl.append(q)
    # a dead native-container subclass still copies silently
    # (BUGS.md#dead-native-like-source-own-slot-copies)
    bump_pt(pl)  # tpyc: ok
    print("free PList", q.v)


class Driver:
    def run(self) -> None:
        # method body
        reset()
        bump(View())  # tpyc: warning(/copies list\[int32\] elements/)
        print("method", G)


def gen_body() -> Iterator[int32]:
    # generator body
    reset()
    bump(View())  # tpyc: warning(/copies list\[int32\] elements/)
    print("generator", G)
    yield 1


async def async_body() -> None:
    # async body
    reset()
    bump(View())  # tpyc: warning(/copies list\[int32\] elements/)
    await asyncio.sleep(0)
    print("async", G)


def main() -> None:
    free_function(True)
    Driver().run()
    for _ in gen_body():
        pass
    asyncio.run(async_body())


main()
