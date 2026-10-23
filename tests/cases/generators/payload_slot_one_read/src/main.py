# A generator's yield converts its value as `return` does: fresh and last-use
# sources move out of the frame, references alias. Each section mutates
# what it received and prints whether the mutation reached the original.
from typing import Callable, Iterator
from tpy import Own, Ptr, StrView, auto_readonly, int32, nocopy, take_ptr


class A:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


@nocopy
class N:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class P:
    kid: A

    def __init__(self, v: int32) -> None:
        self.kid = A(v)


class Ctx:
    def __enter__(self) -> int32:
        print("with enter")
        return 1

    def __exit__(self, et, ev, tb) -> None:
        print("with exit")


class H:
    buf: list[int32]

    def __init__(self) -> None:
        self.buf = [1]


class C:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class Holder:
    p: tuple[int32, C]

    def __init__(self, p: Own[tuple[int32, C]]) -> None:
        self.p = p

    def relay(self) -> Iterator[tuple[int32, C]]:
        for i in range(1):
            # a field tuple of self lends its element
            yield self.p  # tpyc: ok
            yield self.p


class W:
    _a: Ptr[A]

    def __init__(self, a: Ptr[A]) -> None:
        self._a = a

    def each(self) -> Iterator[A]:
        # a Ptr field hands out its pointee
        yield self._a  # tpyc: ok
        yield self._a


def make_mixed(n: int32, b: A) -> tuple[Own[A], A]:
    return (A(n), b)


def owned() -> tuple[Own[A], int32]:
    return (A(6), 1)


def make() -> Own[A]:
    return A(7)


def read() -> str:
    return "text"


def first(s: str) -> StrView | None:
    if len(s) == 0:
        return None
    return s[0:2]


picks: list[int32] = []


def pick(c: bool) -> bool:
    picks.append(1)
    return c


def ot_dead() -> Iterator[Own[tuple[A, int32]]]:
    a = A(1)
    # a dead local moves into the owned tuple
    yield (a, 3)  # tpyc: ok
    yield (A(2), 4)


def ot_owned_call() -> Iterator[Own[tuple[A, int32]]]:
    # an owning call passes
    yield owned()  # tpyc: ok
    yield owned()


def ot_fresh() -> Iterator[Own[tuple[A, int32]]]:
    # a fresh member
    yield (A(1), 3)  # tpyc: ok
    yield (A(2), 3)


def ot_last_use() -> Iterator[Own[tuple[A, int32]]]:
    u = (A(2), 3)
    # an owned name at its last use moves
    yield u  # tpyc: ok
    yield (A(5), 5)


def own_one() -> Iterator[Own[A]]:
    a = A(1)
    # the scalar twin moves its last-use local
    yield a  # tpyc: ok
    yield make()


def own_nocopy() -> Iterator[Own[N]]:
    n = N(1)
    # a non-copyable local moves out
    yield n  # tpyc: ok
    yield N(2)


def str_last() -> Iterator[str]:
    s = read()
    # a last-use str moves
    yield s  # tpyc: ok
    yield "x"


def ref_field(p: P) -> Iterator[A]:
    # a parameter's field hands out a reference
    yield p.kid  # tpyc: ok
    yield p.kid


def ref_ternary(p: A, q: A, c: bool) -> Iterator[A]:
    # the reference ternary picks one parameter, its guard evaluated once
    yield p if pick(c) else q  # tpyc: ok
    yield q


def bt_frame() -> Iterator[tuple[A, int32]]:
    t = owned()
    # the frame's tuple is lent, as its scalar twin's local is
    yield t  # tpyc: ok
    print("bt_frame resumed", t[0].v)
    t = owned()
    yield t


def inverse_bt(p: A) -> Iterator[tuple[A, int32]]:
    # a borrow-form tuple of a parameter aliases it
    yield (p, 3)  # tpyc: ok
    yield (p, 4)


def inverse_to() -> Iterator[tuple[Own[A], int32]]:
    a = A(1)
    # a per-element Own slot moves the dead local
    yield (a, 3)  # tpyc: ok
    yield (A(2), 3)


def gen_t[T](x: T) -> Iterator[T]:
    # an open T parameter is lent at a reference instantiation
    yield x  # tpyc: ok
    yield x


def own_make() -> Iterator[Own[A]]:
    # a fresh value at an owned slot
    yield make()  # tpyc: ok
    yield make()


def in_finally(p: A) -> Iterator[A]:
    try:
        # a yield inside try; the finally reads the parameter after it
        yield p  # tpyc: ok
    finally:
        print("in_finally fin", p.v)


def in_with(p: A) -> Iterator[A]:
    with Ctx():
        # a yield in a with body
        yield p  # tpyc: ok


def view_first(s: str) -> Iterator[StrView | None]:
    # a view of the frame's own copy of s
    yield first(s)  # tpyc: ok
    yield None


def subscript(xs: list[A]) -> Iterator[A]:
    # a container element hands out a reference into the container
    yield xs[0]  # tpyc: ok
    yield xs[1]


def container_field(h: H) -> Iterator[list[int32]]:
    # a parameter's container field is lent
    yield h.buf  # tpyc: ok
    yield h.buf


def lambdas(n: int32) -> Iterator[Callable[[int32], int32]]:
    # a closure value
    yield lambda x: x + n  # tpyc: ok
    yield lambda x: x + n


def rebound_whole(v: A) -> Iterator[tuple[A, A]]:
    p = make_mixed(1, v)
    # the frame's mixed tuple is lent whole, then rebound
    yield p  # tpyc: ok
    p = make_mixed(2, v)
    yield p


def str_param(s: str) -> Iterator[str]:
    # the frame's own copy of a str parameter
    yield s  # tpyc: ok
    yield s


def in_match(k: int32, p: A) -> Iterator[A]:
    match k:
        case 1:
            # a yield in a match arm
            yield p  # tpyc: ok
        case _:
            yield p


@nocopy
class NC:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class B:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


def nocopy_literal() -> Iterator[Own[tuple[NC, int32]]]:
    n = NC(1)
    # a non-copyable member of an owned tuple literal moves in
    yield (n, 3)  # tpyc: ok


def mixed_param(b: B) -> Iterator[tuple[Own[A], B]]:
    t = (A(1), b)
    # a mixed tuple at its last use moves the element the step owns and
    # lends the caller's one it borrows
    yield t  # tpyc: ok


def own_live(b: B) -> Iterator[Own[tuple[A, B]]]:
    t = (A(1), b)
    # a pointer tuple still read after the yield: the owned step copies both
    # elements out of it (the storage lift)
    yield t  # tpyc: warning(/copies A into owned storage/) warning(/copies B into owned storage/)
    # the frame's tuple and the caller's B stay usable after the copy
    t[0].v += 5
    b.v += 5
    print("own_live after", t[0].v, b.v)


def union_str(s: str) -> Iterator[A | str]:
    # a str parameter at a union slot with a record member
    yield s  # tpyc: ok


def lists() -> Iterator[Own[list[int32]]]:
    # a list literal spells its type: the step is no bare brace's target
    yield [1, 2]  # tpyc: ok
    yield [3]


def bytes_param(b: bytes) -> Iterator[bytes]:
    # the frame's own copy of a bytes parameter
    yield b  # tpyc: ok
    yield b


def opt_str_param(s: str | None) -> Iterator[str | None]:
    # the frame's own copy of an optional str parameter
    yield s  # tpyc: ok
    yield s


def value_union(x: int32, s: str) -> Iterator[int32 | str]:
    # value members of a union slot
    yield x  # tpyc: ok
    yield s


class Inner:
    a: A

    def __init__(self) -> None:
        self.a = A(1)

    @auto_readonly
    def get(self) -> A:
        return self.a


class Outer:
    b: Inner

    def __init__(self) -> None:
        self.b = Inner()


def accessor(o: Outer) -> Iterator[A]:
    # an accessor's borrow, rooted through its receiver
    yield o.b.get()  # tpyc: ok


def mixed_fin(b: B) -> tuple[Own[A], B]:
    t = (A(1), b)
    try:
        # a mixed tuple returned under a finally that writes through it is
        # captured after the chain, as its scalar twin is
        return t  # tpyc: ok
    finally:
        t[0].v = 99


def mixed_fin_nocopy(b: B) -> tuple[Own[NC], B]:
    t = (NC(1), b)
    try:
        # the non-copyable twin moves after the chain too
        return t  # tpyc: ok
    finally:
        t[0].v = 99


def opt_section() -> None:
    g = opt_str_param("x")
    try:
        print("opt_str_param", next(g))
        print("opt_str_param", next(g))
    except StopIteration:
        print("opt_str_param done")


class L:
    v: list[int32]

    def __init__(self, v: int32) -> None:
        self.v = [v]


def opt_owned() -> Iterator[Own[L | None]]:
    a: L | None = L(1)
    # an owned optional record step
    yield a  # tpyc: ok
    yield None


def opt_a() -> Iterator[Own[A | None]]:
    a: A | None = A(7)
    yield a
    yield None


def keep_opts() -> int32:
    out: list[L | None] = []
    # the step consumed without a None test
    for o in opt_owned():
        out.append(o)
    return len(out)


def nc_steps() -> Iterator[Own[tuple[NC, int32]]]:
    yield (NC(1), 3)
    yield (NC(2), 4)


def dict_comp_section() -> None:
    # a dict comprehension's value moves the owned step's element out
    d = {q[1]: q[0] for q in ot_owned_call()}  # tpyc: ok
    print("dict_comp", d[1].v)
    # the non-copyable twin builds only because it moves
    dn = {q[1]: q[0] for q in nc_steps()}  # tpyc: ok
    print("dict_comp_nocopy", dn[4].v)


def str_steps(s: str) -> Iterator[Own[tuple[str, int32]]]:
    yield (s, 1)


def list_comp_section() -> None:
    # a list comprehension's element moves the owned step's element out
    xs = [q[0] for q in ot_owned_call()]  # tpyc: ok
    xs[0].v += 1
    print("list_comp", xs[0].v, xs[1].v)
    # the non-copyable twin builds only because it moves
    ns = [q[0] for q in nc_steps()]  # tpyc: ok
    print("list_comp_nocopy", ns[0].v, ns[1].v)
    # a negative index names the same element
    ng = [q[-2] for q in nc_steps()]  # tpyc: ok
    print("list_comp_negative", ng[0].v)
    # an owned str element moves too (the generated C++ pins the move)
    st = [q[0] for q in str_steps("abcdefghijklmnopqrstuvwxyz")]  # tpyc: ok
    print("list_comp_str", st[0])
    # an owned optional step filtered on its None test (the filter does not
    # narrow: BUGS.md#comp-filter-none-test-not-narrowing)
    ls = [o.v for o in opt_a() if o is not None]  # tpyc: warning(/Potential None access/)
    print("list_comp_opt", ls)


def gen_expr_section() -> None:
    xs = [A(1), A(2)]
    # a generator expression's element is its yield: it lends the element
    for gx in (b for b in xs):  # tpyc: ok
        gx.v = 0
    print("genexpr", xs[0].v, xs[1].v)


def view_section() -> None:
    g = view_first("hello")
    try:
        print("view_first", next(g))
        print("view_first", next(g))
    except StopIteration:
        print("view_first done")


def main() -> None:
    for q in ot_dead():
        # the owned tuple's member reads through the storage
        q[0].v = 9  # tpyc: ok
        print("ot_dead", q[0].v, q[1])
    for q in ot_owned_call():
        q[0].v += 1
        print("ot_owned_call", q[0].v)
    for q in ot_fresh():
        q[0].v += 10
        print("ot_fresh", q[0].v)
    for q in ot_last_use():
        q[0].v += 10
        print("ot_last_use", q[0].v)
    for a in own_one():
        a.v += 100
        print("own_one", a.v)
    for n in own_nocopy():
        n.v += 100
        print("own_nocopy", n.v)
    for s in str_last():
        print("str_last", s)
    p = P(1)
    for k in ref_field(p):
        k.v += 1
    print("ref_field", p.kid.v)
    x = A(1)
    y = A(2)
    for r in ref_ternary(x, y, True):
        r.v += 10
    print("ref_ternary", x.v, y.v, len(picks))
    for t in bt_frame():
        t[0].v += 1
        print("bt_frame", t[0].v)
    z = A(1)
    for t in inverse_bt(z):
        t[0].v += 1
    print("inverse_bt", z.v)
    for t in inverse_to():
        t[0].v += 1
        print("inverse_to", t[0].v)
    w = A(1)
    for g in gen_t(w):
        g.v += 1
    print("gen_t record", w.v)
    for gs in gen_t("s"):
        print("gen_t str", gs)
    for gi in gen_t(5):
        print("gen_t int", gi)
    for m in own_make():
        m.v += 1
        print("own_make", m.v)
    f = A(1)
    for fa in in_finally(f):
        fa.v = 9
    print("in_finally", f.v)
    h = A(1)
    for ha in in_with(h):
        ha.v = 4
    print("in_with", h.v)
    gen_expr_section()
    view_section()
    # an owned tuple step read inside a comprehension
    print("ot_comp", [q[0].v for q in ot_dead()])  # tpyc: ok
    for nq in nocopy_literal():
        nq[0].v += 1
        print("nocopy_literal", nq[0].v)
    bb = B(2)
    for mq in mixed_param(bb):
        mq[0].v += 1
        mq[1].v += 1
        print("mixed_param", mq[0].v, bb.v)
    lb = B(2)
    for lq in own_live(lb):
        # read before resuming: the warned copies are observable only
        # through a write on one side, which CPython would share
        print("own_live", lq[0].v, lq[1].v)
    print("own_live main", lb.v)
    for us in union_str("ok"):
        if isinstance(us, str):
            print("union_str", us)
    for xs2 in lists():
        print("lists", len(xs2))
    for by in bytes_param(b"ab"):
        print("bytes_param", len(by))
    opt_section()
    for oo in opt_owned():
        # the None test reads the owned optional step
        if oo is not None:  # tpyc: ok
            oo.v.append(9)
            print("opt_owned", oo.v)
    print("keep_opts", keep_opts())
    dict_comp_section()
    list_comp_section()
    for vu in value_union(1, "two"):
        print("value_union", vu)
    ob = Outer()
    for oa in accessor(ob):
        oa.v = 6
    print("accessor", ob.b.a.v)
    print("mixed_fin", mixed_fin(bb)[0].v)
    print("mixed_fin_nocopy", mixed_fin_nocopy(bb)[0].v)
    xs = [A(1), A(2)]
    for e in subscript(xs):
        e.v += 5
    print("subscript", xs[0].v, xs[1].v)
    hb = H()
    for got in container_field(hb):
        got.append(2)
    print("container_field", len(hb.buf))
    hold = Holder((1, C(2)))
    for n2, c in hold.relay():
        c.v += 1
    print("self_field", hold.p[1].v)
    for fn in lambdas(10):
        print("lambdas", fn(1))
    rg = rebound_whole(A(5))
    for t2 in rg:
        a2 = t2[0]
        for u2 in rg:
            print("rebound_whole", a2.v, u2[0].v)
    for ss in str_param("abc"):
        print("str_param", ss)
    pa = A(1)
    pw = W(take_ptr(pa))
    for px in pw.each():
        px.v += 1
    print("ptr_field", pa.v)
    mp = A(1)
    for ma in in_match(1, mp):
        ma.v = 3
    print("in_match", mp.v)


main()
