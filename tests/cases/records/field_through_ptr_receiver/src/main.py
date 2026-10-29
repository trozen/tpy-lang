# Fields written and iterated through any receiver the shared readers admit
# (a Ptr, an element, a field chain); each write is observed via the owner.
from typing import Any, Iterator
from tpy import Own, Ptr, Span, int32, take_ptr


class R:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class Bag:
    xs: list[int32]

    def __init__(self) -> None:
        self.xs = [1, 2]

    def __iter__(self) -> Iterator[int32]:
        for x in self.xs:
            yield x


class K:
    r: R
    o: R | None
    items: list[int32]
    grid: list[list[int32]]
    c: Bag

    def __init__(self) -> None:
        self.r = R(0)
        self.o = R(1)
        self.items = [1]
        self.grid = []
        self.c = Bag()


class V:
    a: Any
    sp: Span[int32]

    def __init__(self, sp: Span[int32]) -> None:
        self.a = 0
        self.sp = sp


class N:
    s: str | None

    def __init__(self, s: str) -> None:
        self.s = s


class L:
    pk: Ptr[K]

    def __init__(self, k: K) -> None:
        self.pk = k
        # constructor: a record written through a Ptr field of self
        self.pk.r = R(30)  # tpyc: ok

    def set(self, v: int32) -> None:
        # method: the same write off self's Ptr field
        self.pk.r = R(v)  # tpyc: ok


class W:
    k: K

    def __init__(self) -> None:
        self.k = K()


class Inner:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class Mid:
    opt: Inner | None

    def __init__(self) -> None:
        self.opt = Inner(4)

    def empty(self) -> bool:
        return self.opt is None


class Slot:
    opt: Inner | None
    mid: Mid

    def __init__(self) -> None:
        self.opt = None
        self.mid = Mid()


class Holder:
    slots: list[Slot]

    def __init__(self) -> None:
        self.slots = [Slot()]

    def fill(self, i: int32) -> None:
        # list element: a fresh record into an Optional field of the element
        self.slots[i].opt = Inner(1)  # tpyc: ok

    def clear(self, i: int32) -> None:
        # field chain over a list element: None clears the Optional
        self.slots[i].mid.opt = None  # tpyc: ok


def write_param(p: Ptr[K]) -> None:
    # free function, Ptr param: record, Optional record, None, list
    p.r = R(5)  # tpyc: ok
    p.o = R(6)  # tpyc: ok
    p.items = [7, 8]  # tpyc: ok


def clear_param(p: Ptr[K]) -> None:
    # free function, Ptr param: an Optional field cleared
    p.o = None  # tpyc: ok


def clear_str(p: Ptr[N], ns: list[N]) -> None:
    # an Optional[str] field cleared through a Ptr and a container element
    p.s = None  # tpyc: ok
    ns[0].s = None  # tpyc: ok


def write_moved(p: Ptr[K]) -> None:
    r = R(20)
    # a name source at its last use moves into the field
    p.r = r  # tpyc: ok


def write_null(p: Ptr[K]) -> None:
    try:
        # a null receiver raises as CPython's None does
        p.r = R(1)  # tpyc: ok
    except AttributeError:
        print("null")


def write_slots(p: Ptr[V], sp: Span[int32]) -> None:
    # an Any slot and a Span view slot through a Ptr
    p.a = 5  # tpyc: ok
    p.sp = sp  # tpyc: ok


def make_grid(n: int32) -> Own[list[list[int32]]]:
    return [[n]]


def find(k: K) -> Ptr[K]:
    return take_ptr(k)


def write_local(k: K) -> None:
    p = find(k)
    r = R(9)
    # Ptr local, a name source: the field copies it, as off a param
    p.r = r  # tpyc: warning(/copies R into field/)
    print("local", p.r.v, r.v)


def write_ptr_field(m: L) -> None:
    # a Ptr held in a record field
    m.pk.r = R(11)  # tpyc: ok


def write_elem(xs: list[Ptr[K]]) -> None:
    # a Ptr container element
    xs[0].r = R(12)  # tpyc: ok


def write_chain(w: W) -> None:
    # field over field
    w.k.r = R(13)  # tpyc: ok


def write_nested(w: W, n: int32) -> None:
    # field chain: a comprehension into a list field, a record into an
    # Optional field
    w.k.items = [j * 2 for j in range(n)]  # tpyc: ok
    w.k.o = R(15)  # tpyc: ok
    # ... an owned call result and a nested list literal into a list field
    w.k.grid = make_grid(n)  # tpyc: ok
    w.k.grid = [[1], [n]]  # tpyc: ok


def write_rows(ks: list[K], t: tuple[K, int32]) -> None:
    # container element: a record and a list field
    ks[0].r = R(16)  # tpyc: ok
    ks[0].items = [17]  # tpyc: ok
    # tuple element
    t[0].r = R(18)  # tpyc: ok


def gen_writes(p: Ptr[K]) -> Iterator[int32]:
    # generator: the write between two suspensions
    yield p.r.v
    p.r = R(14)  # tpyc: ok
    yield p.r.v


def gen_iter(p: Ptr[K]) -> Iterator[int32]:
    # generator: for over a user iterable through a Ptr
    for x in p.c:  # tpyc: ok
        yield x


def iterate(p: Ptr[K]) -> None:
    # for over a user iterable through a Ptr param
    for x in p.c:  # tpyc: ok
        print("for param", x)
    q = find_bag_owner(p)
    # ... and through a Ptr local
    for y in q.c:  # tpyc: ok
        print("for local", y)
    # a container constructor and a comprehension over the same field
    print("list", list(p.c))  # tpyc: ok
    print("comp", [z * 10 for z in p.c])  # tpyc: ok
    # a list field through the Ptr: list() and a comprehension
    print("list field", list(p.items))  # tpyc: ok
    print("comp field", [z + 1 for z in p.items])  # tpyc: ok
    # a generator expression over the same field
    print("genexpr field", sum(z for z in p.items))  # tpyc: ok


def find_bag_owner(p: Ptr[K]) -> Ptr[K]:
    return p


def main() -> None:
    k = K()
    write_param(take_ptr(k))
    k.r.v += 100
    o = k.o
    print("param", k.r.v, o.v if o is not None else -1, k.items)
    clear_param(take_ptr(k))
    print("param clear", k.o is None)

    n0 = N("a")
    ns = [N("b")]
    clear_str(take_ptr(n0), ns)
    n1 = ns[0]
    print("str clear", n0.s is None, n1.s is None)

    write_local(k)
    print("local owner", k.r.v)

    m = L(k)
    print("ctor", k.r.v)
    m.set(31)
    print("method", k.r.v)
    write_ptr_field(m)
    print("ptr field", k.r.v)

    xs: list[Ptr[K]] = []
    xs.append(take_ptr(k))
    write_elem(xs)
    print("elem", k.r.v)

    w = W()
    write_chain(w)
    w.k.r.v += 1
    print("chain", w.k.r.v)

    write_nested(w, 3)
    wk = w.k
    wk.items.append(9)
    wo = wk.o
    wk.grid.append([5])
    print("nested", wk.items, wo.v if wo is not None else -1, wk.grid)

    ks = [K()]
    write_rows(ks, (k, 0))
    k0 = ks[0]
    k0.r.v += 1
    k0.items.append(1)
    print("rows", k0.r.v, k0.items, k.r.v)

    for v in gen_writes(take_ptr(k)):
        print("gen", v)
    print("gen owner", k.r.v)

    h = Holder()
    h.fill(0)
    s = h.slots[0].opt
    print("slot fill", s.v if s is not None else -1)
    h.clear(0)
    print("slot clear", h.slots[0].mid.empty())

    write_moved(take_ptr(k))
    k.r.v += 1
    print("moved", k.r.v)
    write_null(None)

    ys = [1, 2]
    zs = [7]
    vv = V(ys)
    write_slots(take_ptr(vv), zs)
    zs[0] = 8
    print("slots", vv.sp[0])

    k.c.xs.append(3)
    k.items.append(2)
    iterate(take_ptr(k))
    for g in gen_iter(take_ptr(k)):
        print("gen for", g)


main()
