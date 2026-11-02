# A property store through an auto-deref receiver (Ptr, a Deref wrapper, a
# lock guard) calls the setter, as a getter read through one calls the getter.
from typing import Iterator
from tpy import int32, Ptr
from tplib.box import Box as HBox
from tplib.rc import Rc
from tpy.sync import Mutex


class Box:
    _v: int32

    def __init__(self) -> None:
        self._v = 0

    @property
    def v(self) -> int32:
        return self._v

    @v.setter
    def v(self, value: int32) -> None:
        self._v = value * 10


class Inner:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class G[T]:
    _v: T

    def __init__(self, v: T) -> None:
        # expected: a generic T stored into a field may be a reference type
        self._v = v  # tpyc: warning(/may copy T into field/)

    @property
    def v(self) -> T:
        return self._v

    @v.setter
    def v(self, value: T) -> None:
        self._v = value


class Poker:
    k: int32

    def __init__(self, k: int32) -> None:
        self.k = k

    def poke(self, p: Ptr[Box]) -> None:
        # method body: setter through a nullable Ptr param
        p.v = self.k  # tpyc: nullable(p)


def via_ptr(p: Ptr[Box]) -> None:
    # nullable Ptr param: the setter runs past the None check
    p.v = 1  # tpyc: nullable(p)


def via_ptr_local(b: Box) -> None:
    q: Ptr[Box] = b
    print("ptr_local before", q.v)
    # proven non-null by the read above: no None check
    q.v = 2  # tpyc: non_null(q)


def via_hbox(h: HBox[Box]) -> None:
    # tplib Box deref
    h.v = 5  # tpyc: ok


def via_g_int_ptr(p: Ptr[G[int32]]) -> None:
    # generic setter at int32 through Ptr
    p.v = 13  # tpyc: nullable(p)


def via_g_rec_ptr(p: Ptr[G[Inner]]) -> None:
    # generic setter at a record T through Ptr
    p.v = Inner(14)  # tpyc: nullable(p)


def gen_store(p: Ptr[Box]) -> Iterator[int32]:
    # generator body: setter through Ptr
    p.v = 8  # tpyc: nullable(p)
    yield 0


def via_opt(o: Box | None) -> None:
    if o is not None:
        # inverse: Optional-narrowed receiver
        o.v = 4  # tpyc: ok


def main() -> None:
    b = Box()
    via_ptr(b)
    print("ptr", b.v)

    b2 = Box()
    via_ptr_local(b2)
    print("ptr_local", b2.v)

    h = HBox(Box())
    via_hbox(h)
    print("hbox", h.get().v)

    r = Rc.new(Box())
    r2 = r.clone()
    # Rc deref: the store is seen through the clone
    r.v = 6  # tpyc: ok
    print("rc", r2.v)

    rg = Rc.new(G(1))
    rg2 = rg.clone()
    # generic setter through a Deref wrapper: the store is seen through the clone
    rg.v = 7  # tpyc: ok
    print("rc_generic", rg2.v)

    m = Mutex.new(Box())
    with m.lock() as g:
        # Mutex guard deref
        g.v = 7  # tpyc: ok
    with m.lock() as g2:
        print("mutex", g2.v)
        # A payload's private field written through the guard reaches the
        # payload, under CPython's stub as under TPy's deref.
        g2._v = 8
    with m.lock() as g3:
        print("mutex_private", g3._v)

    gi = G(1)
    # generic setter at int32, plain receiver
    gi.v = 11  # tpyc: ok
    pgi: Ptr[G[int32]] = gi
    print("g_int", pgi.v)
    via_g_int_ptr(gi)
    print("g_int_ptr", gi.v)

    gr = G(Inner(0))
    # generic setter at a record T, plain receiver
    gr.v = Inner(12)  # tpyc: ok
    pgr: Ptr[G[Inner]] = gr
    print("g_rec", pgr.v.n)
    via_g_rec_ptr(gr)
    print("g_rec_ptr", gr.v.n)

    b3 = Box()
    Poker(9).poke(b3)
    print("method", b3.v)

    b4 = Box()
    for _ in gen_store(b4):
        pass
    print("generator", b4.v)

    b5 = Box()
    # inverse: plain receiver
    b5.v = 3  # tpyc: ok
    p5: Ptr[Box] = b5
    print("plain", p5.v)

    b6 = Box()
    via_opt(b6)
    print("opt", b6.v)

    # inverse: getter reads through each path
    print("getters", b.v, h.v, r.v, gi.v, gr.v.n)


main()
