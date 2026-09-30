# Field writes that copy where CPython aliases, each warned: every write is
# followed by a mutation and both sides are printed, pinning TPy's copy.
from tpy import Own, int32


class P:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


def mk(v: int32) -> Own[P]:
    return P(v)


class Src:
    v: P

    def __init__(self, v: Own[P]) -> None:
        self.v = v

    def peek(self) -> P:
        return self.v


class Q:
    w: int32

    def __init__(self, w: int32) -> None:
        self.w = w


class Holder:
    p: P
    xs: list[int32]
    ys: list[int32]
    items: list[int32] | None
    byval: P | None

    def __init__(self, xs: list[int32]) -> None:
        # ctor: an `or` with an existing-object operand copies it.
        self.xs = xs or [0]  # tpyc: warning(/copies list\[int32\] into field/)
        self.ys = []
        self.p = P(0)
        self.items = None
        self.byval = None

    def writes(self, xs: list[int32], items: list[int32] | None,
               s: Src) -> None:
        # method: a walrus names a binding the write copies.
        self.p = (q := mk(1))  # tpyc: warning(/copies P into field/)
        q.v = 50
        print("method.walrus", self.p.v, q.v)
        # method: an `and` whose result may be the existing operand.
        self.ys = [7] and xs  # tpyc: warning(/copies list\[int32\] into field/)
        xs.append(95)
        print("method.and", self.ys, xs)
        # method: a borrowed Optional container param.
        self.items = items  # tpyc: warning(/copies list\[int32\] \| None into field/)
        if items is not None:
            items.append(3)
        print("method.opt_param", self.items, items)
        # method: a borrow-returning accessor into an Optional record slot.
        self.byval = s.peek()  # tpyc: warning(/copies P into field/)
        s.v.v = 77
        byval = self.byval
        if byval is not None:
            print("method.accessor", byval.v, s.v.v)


def holder(xs: list[int32]) -> None:
    h = Holder([1])
    # local holder: a walrus and an `or`, then the sources are mutated.
    h.p = (q := mk(2))  # tpyc: warning(/copies P into field/)
    q.v = 51
    h.xs = xs or []  # tpyc: warning(/copies list\[int32\] into field/)
    xs.append(96)
    print("holder.walrus_or", h.p.v, q.v, h.xs, xs)


class Built:
    mine: P
    u: P | Q
    a: P
    b: P

    def __init__(self, s: Src, a: P) -> None:
        # ctor: a borrow-returning method, a member NAME into a union, a
        # record param and a read of a field the list already set.
        self.mine = s.peek()  # tpyc: warning(/copies P into field/)
        self.u = a  # tpyc: warning(/copies P into field/)
        self.a = a  # tpyc: warning(/copies P into field/)
        self.b = self.a  # tpyc: warning(/copies P into field/)


class Base:
    z: int32

    def __init__(self) -> None:
        self.z = 0


class Item:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class Derived(Base):
    ib: Item
    ia: Item

    def __init__(self, it: Own[Item]) -> None:
        super().__init__()
        # ctor with a base: own fields are laid out in assignment order, so
        # the first write copies (warned) and the last one moves.
        self.ia = it  # tpyc: warning(/copies Item into field/)
        self.ib = it  # tpyc: ok


class Slot:
    f: list[int32]

    def __init__(self) -> None:
        self.f = []


# nested def: a captured Own param is copied, never moved -- so `nested`
# itself never consumes it.
def nested(p: Own[list[int32]]) -> None:  # tpyc: warning(/never consumed/)
    h = Slot()

    def setit() -> None:
        h.f = p  # tpyc: warning(/copies list\[int32\] into field/)
    setit()
    h.f.append(9)
    setit()
    print("nested.captured", h.f, len(p))


class Opt:
    op: P | None

    def __init__(self) -> None:
        self.op = None

    def elem(self, ps: list[P]) -> None:
        # method: a borrowed container element into an Optional record field.
        self.op = ps[0]  # tpyc: warning(/copies P into field/)
        ps[0].v = 70
        op = self.op
        if op is not None:
            print("method.opt_elem", op.v, ps[0].v)


def own_args(xs: list[int32]) -> None:
    # Own param: an `or` whose result may be the existing operand.
    ys = keep_list(xs or [0])  # tpyc: warning(/copies list\[int32\] into owned storage/)
    xs.append(81)
    print("own_arg.or", ys, xs)


def keep_list(xs: Own[list[int32]]) -> Own[list[int32]]:
    return xs


class Many:
    xs: list[P]
    d: dict[int32, P]

    def __init__(self, p: Own[P]) -> None:
        # ctor: a comprehension element reading an Own param copies it once
        # per element, UNWARNED (BUGS.md#comp-element-move-inside-loop).
        self.xs = [p for _ in range(3)]
        self.d = {}

    def reset(self, p: Own[P], r: P) -> None:
        # method: the same element copies unwarned; a dict-comp value from an
        # outer record name copies with the warning.
        self.xs = [p for _ in range(2)]
        self.d = {i: r for i in range(2)}  # tpyc: warning(/copies P into owned storage/)


def comp_elems() -> None:
    m = Many(P(1))
    m.xs[0].v = 9
    print("ctor.comp_elem", m.xs[0].v, m.xs[1].v)
    r = P(5)
    m.reset(P(2), r)
    m.xs[0].v = 8
    r.v = 6
    print("method.comp_elem", m.xs[0].v, m.xs[1].v, m.d[0].v, m.d[1].v)


def main() -> None:
    xs = [1]
    h = Holder(xs)
    xs.append(94)
    print("ctor.or", h.xs, xs)
    items: list[int32] | None = [1, 2]
    src = Src(P(4))
    h.writes(xs, items, src)
    holder(xs)
    s = Src(P(3))
    a = P(6)
    built = Built(s, a)
    s.v.v = 30
    a.v = 60
    built.a.v = 61
    u = built.u
    if isinstance(u, P):
        print("ctor.copies", built.mine.v, s.v.v, u.v, built.a.v, built.b.v,
              a.v)
    d = Derived(Item(5))
    d.ia.v = 55
    print("ctor.base", d.ia.v, d.ib.v)
    nested([1, 2, 3])
    o = Opt()
    o.elem([P(7)])
    own_args(xs)
    comp_elems()


main()
