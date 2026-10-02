# Field writes that copy where CPython aliases, each warned: every write is
# followed by a mutation and both sides are printed, pinning TPy's copy.
import asyncio
from typing import Iterator
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


class R:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def mk_opt(v: int32) -> Own[P] | None:
    if v > 0:
        return P(v)
    return None


def mk_pq(v: int32) -> Own[P | R]:
    if v > 0:
        return P(v)
    return R(-v)


def bump(p: P | None) -> None:
    if p is not None:
        p.v += 1


def bumpu(u: P | R) -> None:
    if isinstance(u, P):
        u.v += 1
    else:
        u.n += 1


def show(p: P | None) -> int32:
    return -1 if p is None else p.v


def bump_at(xs: list[P | None], i: int32) -> None:
    e = xs[i]
    if e is not None:
        e.v += 1


def show_at(xs: list[P | None], i: int32) -> int32:
    e = xs[i]
    return -1 if e is None else e.v


def bump_key(d: dict[str, P | None], k: str) -> None:
    e = d[k]
    if e is not None:
        e.v += 1


def show_key(d: dict[str, P | None], k: str) -> int32:
    e = d[k]
    return -1 if e is None else e.v


def showu(u: P | R) -> int32:
    return u.v if isinstance(u, P) else -u.n


def bumpu_at(us: list[P | R], i: int32) -> None:
    e = us[i]
    if isinstance(e, P):
        e.v += 1
    else:
        e.n += 1


def showu_at(us: list[P | R], i: int32) -> int32:
    e = us[i]
    return e.v if isinstance(e, P) else -e.n


def bumpu_key(d: dict[str, P | R], k: str) -> None:
    e = d[k]
    if isinstance(e, P):
        e.v += 1
    else:
        e.n += 1


def showu_key(d: dict[str, P | R], k: str) -> int32:
    e = d[k]
    return e.v if isinstance(e, P) else -e.n


class OptSrc:
    f: P | None

    def __init__(self, f: Own[P]) -> None:
        self.f = f


class PqSrc:
    f: P | R

    def __init__(self, f: Own[P]) -> None:
        self.f = f

    def set_r(self, r: Own[R]) -> None:
        self.f = r


class Deep:
    a: OptSrc
    b: PqSrc

    def __init__(self, a: Own[OptSrc], b: Own[PqSrc]) -> None:
        self.a = a
        self.b = b


def bump_src(o: OptSrc) -> None:
    e = o.f
    if e is not None:
        e.v += 1


def show_src(o: OptSrc) -> int32:
    e = o.f
    return -1 if e is None else e.v


def bumpu_src(u: PqSrc) -> None:
    e = u.f
    if isinstance(e, P):
        e.v += 1
    else:
        e.n += 1


def showu_src(u: PqSrc) -> int32:
    e = u.f
    return e.v if isinstance(e, P) else -e.n


def bump_deep(h: Deep) -> None:
    bump_src(h.a)


def show_deep(h: Deep) -> int32:
    return show_src(h.a)


def bumpu_deep(h: Deep) -> None:
    bumpu_src(h.b)


def showu_deep(h: Deep) -> int32:
    return showu_src(h.b)


class Slots:
    op: P | None
    pq: P | R
    g2: P | None
    g3: P | R

    def __init__(self, o: OptSrc, u: PqSrc) -> None:
        # ctor: member-inits of an Optional-record and a record-union field
        # from another object's field of the same type copy the storage.
        self.op = o.f  # tpyc: warning(/copies P \| None into field/)
        self.pq = u.f  # tpyc: warning(/copies P \| R into field/)
        self.g2 = mk_opt(2)
        # An owning call into a union field warns a copy that never happens
        # (BUGS.md#own-optional-param-field-store-copies, the CALL twin).
        self.g3 = mk_pq(-3)  # tpyc: warning(/copies P \| R into field/)

    def show_op(self) -> int32:
        e = self.op
        return -1 if e is None else e.v

    def showu_pq(self) -> int32:
        e = self.pq
        return e.v if isinstance(e, P) else -e.n

    def bump_g2(self) -> None:
        e = self.g2
        if e is not None:
            e.v += 1

    def show_g2(self) -> int32:
        e = self.g2
        return -1 if e is None else e.v

    def bumpu_g3(self) -> None:
        e = self.g3
        if isinstance(e, P):
            e.v += 1
        else:
            e.n += 1

    def showu_g3(self) -> int32:
        e = self.g3
        return e.v if isinstance(e, P) else -e.n

    def writes(self, o: OptSrc, u: PqSrc, h: Deep, xs: list[P | None],
               us: list[P | R], d: dict[str, P | None],
               du: dict[str, P | R]) -> None:
        # method: an Optional-record field from a field read, a self field,
        # a chain, a container element, a dict value and an owning call.
        self.op = o.f  # tpyc: warning(/copies P \| None into field/)
        bump_src(o)
        print("method.opt_field", self.show_op(), show_src(o))
        self.op = self.g2  # tpyc: warning(/copies P \| None into field/)
        self.bump_g2()
        print("method.opt_self_field", self.show_op(), self.show_g2())
        self.op = h.a.f  # tpyc: warning(/copies P \| None into field/)
        bump_deep(h)
        print("method.opt_chain", self.show_op(), show_deep(h))
        self.op = xs[0]  # tpyc: warning(/copies P \| None into field/)
        bump_at(xs, 0)
        print("method.opt_elem", self.show_op(), show_at(xs, 0))
        self.op = d["k"]  # tpyc: warning(/copies P \| None into field/)
        bump_key(d, "k")
        print("method.opt_dict", self.show_op(), show_key(d, "k"))
        self.op = mk_opt(9)  # tpyc: ok
        print("method.opt_call", self.show_op())
        # method: the record-union twin of each source.
        self.pq = u.f  # tpyc: warning(/copies P \| R into field/)
        bumpu_src(u)
        print("method.pq_field", self.showu_pq(), showu_src(u))
        self.pq = self.g3  # tpyc: warning(/copies P \| R into field/)
        self.bumpu_g3()
        print("method.pq_self_field", self.showu_pq(), self.showu_g3())
        self.pq = h.b.f  # tpyc: warning(/copies P \| R into field/)
        bumpu_deep(h)
        print("method.pq_chain", self.showu_pq(), showu_deep(h))
        self.pq = us[0]  # tpyc: warning(/copies P \| R into field/)
        bumpu_at(us, 0)
        print("method.pq_elem", self.showu_pq(), showu_at(us, 0))
        self.pq = du["k"]  # tpyc: warning(/copies P \| R into field/)
        bumpu_key(du, "k")
        print("method.pq_dict", self.showu_pq(), showu_key(du, "k"))
        # An owning call into a union field warns a copy that never happens
        # (BUGS.md#own-optional-param-field-store-copies, the CALL twin).
        self.pq = mk_pq(-8)  # tpyc: warning(/copies P \| R into field/)
        print("method.pq_call", self.showu_pq())

    def gen_writes(self, o: OptSrc, us: list[P | R]) -> Iterator[int32]:
        # generator method: the same storage copies inside a frame.
        self.op = o.f  # tpyc: warning(/copies P \| None into field/)
        bump_src(o)
        yield self.show_op()
        yield show_src(o)
        self.pq = us[0]  # tpyc: warning(/copies P \| R into field/)
        bumpu_at(us, 0)
        yield self.showu_pq()
        yield showu_at(us, 0)

    async def async_writes(self, h: Deep, d: dict[str, P | R]) -> int32:
        # async method: a chain and a dict value copy inside a frame.
        self.op = h.a.f  # tpyc: warning(/copies P \| None into field/)
        bump_deep(h)
        self.pq = d["k"]  # tpyc: warning(/copies P \| R into field/)
        bumpu_key(d, "k")
        await asyncio.sleep(0)
        return self.show_op() * 100 + show_deep(h) * 10 + self.showu_pq()


def store_g(s: Slots, g: Own[P] | None) -> None:
    # local holder: an `Own[P] | None` param is the caller's transfer and
    # moves into the field (its `Own[P | None]` spelling still copies and
    # warns: BUGS.md#own-optional-param-field-store-copies).
    s.g2 = g  # tpyc: ok


def slot_holder(o: OptSrc, u: PqSrc, xs: list[P | None]) -> None:
    # local holder: a free function writing a local instance's fields.
    s = Slots(o, u)
    s.op = xs[0]  # tpyc: warning(/copies P \| None into field/)
    bump_at(xs, 0)
    print("holder.opt_elem", s.show_op(), show_at(xs, 0))
    s.pq = mk_pq(4)  # tpyc: warning(/copies P \| R into field/)
    print("holder.pq_call", s.showu_pq())
    s.pq = u.f  # tpyc: warning(/copies P \| R into field/)
    bumpu_src(u)
    print("holder.pq_field", s.showu_pq(), showu_src(u))


def slot_writes() -> None:
    o = OptSrc(P(10))
    u = PqSrc(P(0))
    u.set_r(R(20))
    s = Slots(o, u)
    store_g(s, P(9))
    bump_src(o)
    bumpu_src(u)
    print("ctor.opt_pq", s.show_op(), show_src(o), s.showu_pq(), showu_src(u),
          s.show_g2(), s.showu_g3())
    h = Deep(OptSrc(P(30)), PqSrc(P(40)))
    xs: list[P | None] = [P(50), None]
    us: list[P | R] = [R(60)]
    d: dict[str, P | None] = {"k": P(70)}
    du: dict[str, P | R] = {"k": P(80)}
    s.writes(o, u, h, xs, us, d, du)
    print("gen.opt_pq", list(s.gen_writes(o, us)))
    print("async.opt_pq", asyncio.run(s.async_writes(h, du)))
    slot_holder(o, u, xs)


class Paired:
    t: tuple[int32, P]

    def __init__(self) -> None:
        self.t = (0, P(0))


def literal_list_elem() -> None:
    # local holder: a tuple element of a list whose element type comes from
    # its literal copies into the field like its annotated twin.
    pairs = [(1, P(2))]
    k = Paired()
    k.t = pairs[0]  # tpyc: warning(/copies P into field \(tuple element 1\)/)
    pairs[0][1].v = 9
    print("holder.literal_tuple_elem", k.t[0], k.t[1].v, pairs[0][1].v)


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
    slot_writes()
    literal_list_elem()


main()
