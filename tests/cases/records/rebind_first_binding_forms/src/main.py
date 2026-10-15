# A rebound reference local whose first binding is an unpack target, a walrus
# binder, an owning for target or an @error_return bind keeps its first object
# for the aliases that hold it: a rebind re-points the name, at every position.
from typing import Iterator
from tplib import Box
from tpy import Array, int32, Own, copy, error_return, ReturnException


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Err(Exception, ReturnException):
    pass


def mk(i: int32) -> tuple[Own[P], int32]:
    return (P(i), i)


def mk1(i: int32) -> Own[P]:
    return P(i)


def pairs() -> Iterator[tuple[Own[P], int32]]:
    yield (P(1), 7)


def steps() -> Iterator[Own[P]]:
    yield P(1)


@error_return(Err)
def make(i: int32) -> Own[P]:
    return P(i)


def rows(tag: str) -> None:
    # The rows of one position; each mutates through the alias after the
    # rebind and prints both names.
    p, k = mk(1)
    q = p
    p = P(2)  # tpyc: ok
    q.n += 10
    print(tag, "unpack_rvalue", q.n, p.n, k)
    other = P(100)
    a, j = mk(5)
    b = a
    a = other  # tpyc: ok
    other.n += 5
    print(tag, "unpack_lvalue", b.n, a.n, j)
    if (w := mk1(1)).n > 0:
        v = w
        w = P(2)  # tpyc: ok
        v.n += 10
        print(tag, "walrus_rvalue", v.n, w.n)
    if (x := mk1(6)).n > 0:
        y = x
        x = other  # tpyc: ok
        other.n += 1
        print(tag, "walrus_lvalue", y.n, x.n)
    for s in steps():
        t = s
        s = P(9)  # tpyc: ok
        t.n += 10
        print(tag, "for_own_rvalue", t.n, s.n)
    for c in steps():
        d = c
        c = other  # tpyc: ok
        other.n += 1
        print(tag, "for_own_lvalue", d.n, c.n)


def free_function() -> None:
    rows("free")
    # A for head's unpack target.
    for p, k in pairs():
        q = p
        p = P(2)  # tpyc: ok
        q.n += 10
        print("free", "head_unpack", q.n, p.n, k)


def while_head() -> None:
    # A while head's walrus binds again on each pass: a rebind inside one
    # pass and one after the loop keep the object for the alias.
    i = 0
    while (p := mk1(i)).n < 2:
        q = p
        p = P(50)  # tpyc: ok
        q.n += 10
        i += 1
        print("while", "in_pass", q.n, p.n)
    i = 0
    while (r := mk1(i)).n < 3:
        i += 1
    s = r
    r = P(100)  # tpyc: ok
    s.n += 6
    print("while", "after_loop", s.n, r.n)


def while_head_alias_across_passes() -> None:
    # An alias kept from an earlier pass shares the binder's storage, as its
    # declaration twin's does: the loop slot-reuse warning, copy() to keep it.
    i = 0
    keep = P(-1)
    while (p := mk1(i)).n < 3:
        if i == 0:
            keep = p  # tpyc: warning(/will not keep the object/)
        i += 1
    kept = P(-1)
    i = 0
    while (r := mk1(i)).n < 3:
        if i == 0:
            kept = copy(r)
        i += 1
    # `keep` itself reads the last pass's object (the warned divergence), so
    # only the parity-safe names print.
    print("while", "alias_across_passes", p.n, kept.n, r.n)


class Holder:
    def __init__(self) -> None:
        # Constructor body: the bindings and rebinds written in it.
        p, k = mk(1)
        q = p
        p = P(2)  # tpyc: ok
        q.n += 10
        print("ctor", "unpack_rvalue", q.n, p.n, k)
        other = P(100)
        if (x := mk1(6)).n > 0:
            y = x
            x = other  # tpyc: ok
            other.n += 1
            print("ctor", "walrus_lvalue", y.n, x.n)
        for s in steps():
            t = s
            s = P(9)  # tpyc: ok
            t.n += 10
            print("ctor", "for_own_rvalue", t.n, s.n)

    def method(self) -> None:
        # Method body.
        other = P(100)
        a, j = mk(5)
        b = a
        a = other  # tpyc: ok
        other.n += 5
        print("method", "unpack_lvalue", b.n, a.n, j)
        if (w := mk1(1)).n > 0:
            v = w
            w = P(2)  # tpyc: ok
            v.n += 10
            print("method", "walrus_rvalue", v.n, w.n)
        for c in steps():
            d = c
            c = other  # tpyc: ok
            other.n += 1
            print("method", "for_own_lvalue", d.n, c.n)


def nested() -> None:
    def inner() -> None:
        p, k = mk(1)
        q = p
        p = P(2)  # tpyc: ok
        q.n += 10
        print("nested", "unpack_rvalue", q.n, p.n, k)
        for s in steps():
            t = s
            s = P(9)  # tpyc: ok
            t.n += 10
            print("nested", "for_own_rvalue", t.n, s.n)

    inner()


def in_try() -> None:
    # try body.
    try:
        p, k = mk(1)
        q = p
        p = P(2)  # tpyc: ok
        q.n += 10
        print("try", "unpack_rvalue", q.n, p.n, k)
        if (w := mk1(1)).n > 0:
            v = w
            w = P(2)  # tpyc: ok
            v.n += 10
            print("try", "walrus_rvalue", v.n, w.n)
        other = P(100)
        for c in steps():
            d = c
            c = other  # tpyc: ok
            other.n += 1
            print("try", "for_own_lvalue", d.n, c.n)
    except ValueError:
        print("try", "unreachable")


def in_match(v: int32) -> None:
    match v:
        case 1:
            p, k = mk(1)
            q = p
            p = P(2)  # tpyc: ok
            q.n += 10
            print("match", "unpack_rvalue", q.n, p.n, k)
            if (w := mk1(1)).n > 0:
                x = w
                w = P(2)  # tpyc: ok
                x.n += 10
                print("match", "walrus_rvalue", x.n, w.n)
        case _:
            pass


def in_if(c: bool) -> None:
    if c:
        p, k = mk(1)
        q = p
        p = P(2)  # tpyc: ok
        q.n += 10
        print("if", "unpack_rvalue", q.n, p.n, k)
        for s in steps():
            t = s
            s = P(9)  # tpyc: ok
            t.n += 10
            print("if", "for_own_rvalue", t.n, s.n)


def in_loop() -> None:
    for i in range(2):
        p, k = mk(i)
        q = p
        p = P(20 + i)  # tpyc: ok
        q.n += 10
        print("loop", "unpack_rvalue", q.n, p.n, k)
        if (w := mk1(i)).n >= 0:
            x = w
            w = P(30 + i)  # tpyc: ok
            x.n += 10
            print("loop", "walrus_rvalue", x.n, w.n)


@error_return(Err)
def error_return_bind() -> None:
    # An @error_return call's unwrap as the first binding.
    other = P(5)
    p = make(1)
    q = p
    p = other  # tpyc: ok
    p.n += 100
    print("error_return", "bind_lvalue", q.n, p.n, other.n)


class Outer:
    inner: P

    def __init__(self, n: int32) -> None:
        self.inner = P(n)


def pick(xs: list[P]) -> P | None:
    return xs[0] if xs else None


def declarations(o: Outer) -> None:
    # The declaration's own rows read the same policy: a field alias rebound
    # to a fresh object, an Optional rebound by a call that lends.
    saved = o.inner
    saved = P(23)  # tpyc: ok
    saved.n += 1
    print("decl", "field_alias_rvalue", saved.n, o.inner.n)
    xs = [P(7)]
    z: P | None = P(1)
    z = pick(xs)  # tpyc: ok
    if z is not None:
        z.n += 1
    print("decl", "optional_lent", xs[0].n)


def inverse() -> None:
    # Never rebound: the first binding keeps its plain owning form.
    p, k = mk(3)
    q = p
    q.n += 1
    print("inverse", "unpack_once", p.n, q.n, k)
    if (w := mk1(4)).n > 0:
        x = w
        x.n += 1
        print("inverse", "walrus_once", w.n, x.n)
    # A value-type target copies at every rebind.
    m, j = (1, 2)
    n = m
    m = 5
    print("inverse", "value_unpack", m, n, j)


def own_param(p: Own[P]) -> Own[P]:
    # An Own parameter's rebind with no alias: the plain assign, unchanged.
    p = P(8)
    return p


def nonlocal_rebind() -> None:
    p = P(1)

    def reset() -> None:
        nonlocal p
        p = P(2)

    reset()
    print("inverse", "nonlocal", p.n)


class Pm:
    m: P

    def __init__(self, n: int32) -> None:
        self.m = P(n)


def pair(i: int32) -> tuple[Own[Pm], int32]:
    return (Pm(i), i)


def mkd() -> Own[dict[str, int32]]:
    return {"a": 1}


class L:
    n: int32
    xs: list[int32]

    def __init__(self, n: int32) -> None:
        self.n = n
        self.xs = [n]


def ltake(p: Own[L]) -> Own[L]:
    return p


def lsteps() -> Iterator[Own[L]]:
    for i in range(2):
        yield L(i)


class LentCtx:
    p: P

    def __init__(self) -> None:
        self.p = P(1)

    def __enter__(self) -> P:
        return self.p

    def __exit__(self, a: None, b: None, c: None) -> None:
        pass


class B:
    _v: P
    _xs: list[int32]

    def __init__(self) -> None:
        self._v = P(4)
        self._xs = [1]

    @property
    def v_own(self) -> Own[P]:
        return P(self._v.n)

    @property
    def xs_own(self) -> Own[list[int32]]:
        return [x for x in self._xs]


def with_lent() -> None:
    # A with target over an `__enter__` that lends: a rebind re-points it,
    # the manager's object stays the alias's.
    c = LentCtx()
    with c as p:
        q = p
        p = P(2)  # tpyc: ok
        q.n += 10
        print("with", "lent_rvalue", q.n, p.n, c.p.n)


def loop_escape_hoist() -> None:
    # A loop-body first binding with an alias kept across passes: the slot
    # stands before the loop and the alias shares it, as the declaration
    # twin's does. The alias is taken again on every pass, so the warnings
    # are false here (BUGS.md#loop-slot-reuse-false-warning, face 3); the
    # pass-0 alias read after the loop, where the warning is true, prints
    # the last pass's object and is pinned in records/warn_alias_rebind_clobber.
    holder = P(-1)
    for i in range(2):
        b, j = pair(i)
        holder = b.m  # tpyc: warning(/will not keep the object/)
    holder.n += 100
    print("loop_hoist", "unpack_field", holder.n, j)
    kept = P(-1)
    for i in range(2):
        if (w := mk1(i)).n >= 0:
            kept = w  # tpyc: warning(/will not keep the object/)
    kept.n += 100
    print("loop_hoist", "walrus_if", kept.n)
    keep = P(-1)
    for i in range(3):
        p, k = mk(i)
        keep = p  # tpyc: warning(/will not keep the object/)
    keep.n += 100
    print("loop_hoist", "unpack_keep", keep.n)


def ref_sink_forms(s1: P, l1: list[int32], b1: bytearray) -> None:
    # A first binding from an owning property or a constructor, rebound to
    # an existing object: the write through the name reaches that object.
    b = B()
    x = b.v_own
    x = s1  # tpyc: ok
    x.n += 1
    print("ref_sink", "prop_own_record", s1.n, b._v.n)
    y = b.xs_own
    y = l1  # tpyc: ok
    y.append(9)
    print("ref_sink", "prop_own_list", len(l1), len(b._xs))
    z = bytearray(b"cd")
    z = b1  # tpyc: ok
    z.append(9)
    print("ref_sink", "bytearray_ctor", len(b1))


def elif_head(c: bool) -> None:
    # An elif head's walrus binder.
    if c:
        print("elif", "unreachable")
    elif (p := mk1(1)).n > 0:
        q = p
        p = P(2)  # tpyc: ok
        q.n += 10
        print("elif", "walrus_rvalue", q.n, p.n)


def while_last_pass() -> None:
    # An alias taken on the last pass of a while head's walrus shares the
    # binder's slot (warned); only the parity-safe name prints.
    i = 0
    keep = P(-1)
    while (p := mk1(i)).n < 3:
        keep = p  # tpyc: warning(/will not keep the object/)
        i += 1
    print("while", "alias_last_pass", p.n)


def find(xs: list[P], i: int32) -> P | None:
    if i < len(xs):
        return xs[i]
    return None


def while_borrowed(xs: list[P]) -> None:
    # A while head's walrus that borrows shares no slot: the alias kept from
    # pass 0 is the list's element.
    best: P | None = None
    i = 0
    while (p := find(xs, i)) is not None:  # tpyc: ok
        if i == 0:
            best = p
        i += 1
    if best is not None:
        best.n += 100
    print("while", "borrowed_binder", [x.n for x in xs])


def containers() -> None:
    # A list and a dict local: the unpack target and the walrus binder.
    xs, k = ([1, 2], 3)
    ys = xs
    xs = [7]  # tpyc: ok
    ys.append(5)
    print("containers", "list_unpack", len(xs), len(ys), k)
    if len(w := mkd()) > 0:
        v = w
        w = {"q": 8}  # tpyc: ok
        v["r"] = 3
        print("containers", "dict_walrus", len(w), len(v))


def for_own_to_lvalue() -> None:
    # An owning for target rebound to an existing object and passed to an
    # Own parameter copies (warned), as its declaration twin does: the
    # source keeps its list.
    o = L(9)
    for p in lsteps():
        p = o
        print("for_own", "take", ltake(p).n)  # tpyc: warning(/copies L into owned storage/)
    print("for_own", "after", o.n, o.xs)


def print_arg_copy() -> None:
    # The same consume inside a print argument: the copy reaches the
    # argument (warned), the source keeps its list, and the name still
    # aliases the source after the call.
    other = L(9)
    for p in lsteps():
        p = other
        print("print_arg", "for_own", ltake(p).xs)  # tpyc: warning(/copies L into owned storage/)
        p.xs.append(5)
    print("print_arg", "for_own_source", other.xs)
    # The declaration twin.
    o2 = L(8)
    q = L(1)
    q = o2
    print("print_arg", "decl", ltake(q).xs)  # tpyc: warning(/copies L into owned storage/)
    q.xs.append(6)
    print("print_arg", "decl_source", o2.xs)


def take_then_rebind() -> None:
    # An owning for target consumed by an Own parameter before the body
    # rebinds it: each pass's own step moves into the call (no copy, no
    # warning) and the rebind re-points the name.
    for p in lsteps():
        r = ltake(p)  # tpyc: ok
        p = L(9)
        r.xs.append(1)
        p.xs.append(2)
        print("take_rebind", "in_loop", r.xs, p.xs)


def take_then_rebind_after() -> None:
    # The name rebound to an existing object only after the loop: no pass's
    # binding is rebound, so the consume moves (no warning).
    o = L(5)
    for p in lsteps():
        r = ltake(p)  # tpyc: ok
        r.xs.append(1)
        print("take_rebind", "after_loop", r.xs)
    p = o
    p.xs.append(6)
    print("take_rebind", "after_loop_source", o.xs)


class Hp:
    p: P

    def __init__(self, n: int32) -> None:
        self.p = P(n)

    def get(self) -> P:
        return self.p

    def opt(self) -> P | None:
        return self.p


def mkh(n: int32) -> Own[Hp]:
    return Hp(n)


def temp_receiver_rebind(c: bool) -> None:
    # An owning for target rebound to a borrow out of a temporary receiver
    # dangles, warned (BUGS.md#borrow-dunder-temp-operand-dangles); called
    # with c False, so the dangling read never runs.
    for p in steps():
        if c:
            p = mkh(7).get()  # tpyc: warning(/temporary receiver/)
        q = p
        q.n += 10
        print("temp_receiver", "for_own", p.n, q.n)


def optional_reseat() -> None:
    # A rebound Optional first bound to None, reseated from a call that lends
    # named storage (a named receiver, a named argument): the name points at
    # the lent object, a write through it reaches the source.
    h = Hp(5)
    z: P | None = None
    z = h.opt()  # tpyc: ok
    if z is not None:
        z.n += 10
        print("opt_reseat", "named_receiver", z.n, h.p.n)
    xs = [P(3)]
    y: P | None = None
    y = pick(xs)  # tpyc: ok
    if y is not None:
        y.n += 10
        print("opt_reseat", "named_argument", y.n, xs[0].n)


def optional_reseat_temp_receiver(c: bool) -> None:
    # The same reseat from a temporary receiver dangles, warned
    # (BUGS.md#borrow-dunder-temp-operand-dangles); called with c False, so
    # the dangling read never runs.
    h = Hp(5)
    z: P | None = None
    if c:
        z = mkh(7).opt()  # tpyc: warning(/temporary receiver/)
    else:
        z = h.opt()
    if z is not None:
        z.n += 10
        print("opt_reseat", "temp_receiver", z.n, h.p.n)


class Kinds:
    def __init__(self) -> None:
        pass

    @property
    def d_own(self) -> Own[dict[str, int32]]:
        return {"a": 1}

    @property
    def s_own(self) -> Own[set[int32]]:
        return {1, 2}

    @property
    def a_own(self) -> Own[Array[int32, 2]]:
        return [1, 2]

    @property
    def ba_own(self) -> Own[bytearray]:
        return bytearray(b"ab")

    @property
    def g_own(self) -> Own[Box[P]]:
        return Box(P(1))


def owning_property_kinds(d1: dict[str, int32], s1: set[int32],
                          a1: Array[int32, 2], b1: bytearray,
                          g1: Box[P]) -> None:
    # A first binding from an owning property of each reference kind,
    # rebound to an existing object: the write through the name reaches it.
    k = Kinds()
    d = k.d_own
    d = d1  # tpyc: ok
    d["z"] = 9
    print("prop_own", "dict", len(d1))
    s = k.s_own
    s = s1  # tpyc: ok
    s.add(9)
    print("prop_own", "set", len(s1))
    a = k.a_own
    a = a1  # tpyc: ok
    a[0] = 9
    print("prop_own", "array", a1[0])
    b = k.ba_own
    b = b1  # tpyc: ok
    b.append(9)
    print("prop_own", "bytearray", len(b1))
    g = k.g_own
    g = g1  # tpyc: ok
    g.get().n += 1
    print("prop_own", "generic", g1.get().n)


def owning_property_kinds_call() -> None:
    arr: Array[int32, 2] = [3, 4]
    owning_property_kinds({"q": 1}, {5}, arr, bytearray(b"x"), Box(P(4)))


def declaration_rows(o: P) -> None:
    # A declaration from copy() and from a repeated list, rebound.
    p = copy(o)
    r = p
    p = P(2)  # tpyc: ok
    r.n += 10
    print("decl", "copy_rebound", r.n, p.n, o.n)
    xs = [0] * 3
    zs = xs
    xs = [5]  # tpyc: ok
    zs.append(3)
    print("decl", "repeat_rebound", len(xs), len(zs))


class Ce:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __eq__(self, o: "Ce") -> bool:
        return self.n == o.n


class Cl:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __lt__(self, o: "Cl") -> bool:
        return self.n < o.n


def mke(i: int32) -> tuple[Own[Ce], int32]:
    return (Ce(i), i)


def mkl(i: int32) -> tuple[Own[Cl], int32]:
    return (Cl(i), i)


def mke1(i: int32) -> Own[Ce]:
    return Ce(i)


def mkl1(i: int32) -> Own[Cl]:
    return Cl(i)


def esteps() -> Iterator[Own[Ce]]:
    yield Ce(1)


def lsteps_cl() -> Iterator[Own[Cl]]:
    yield Cl(1)


class CeCtx:
    c: Ce

    def __init__(self) -> None:
        self.c = Ce(1)

    def __enter__(self) -> Ce:
        return self.c

    def __exit__(self, a: None, b: None, c: None) -> None:
        pass


class ClCtx:
    c: Cl

    def __init__(self) -> None:
        self.c = Cl(1)

    def __enter__(self) -> Cl:
        return self.c

    def __exit__(self, a: None, b: None, c: None) -> None:
        pass


def compare_operands() -> None:
    # Each first-binding form rebound to an existing object, which is then
    # mutated through its own name: the comparison reads the rebound object
    # (`==` / `!=` through `__eq__`, `<` through `__lt__`).
    oe = Ce(5)
    ol = Cl(5)
    a, k = mke(1)
    ka = a
    a = oe
    oe.n += 1
    print("compare", "unpack_eq", a == Ce(6), a != ka, k)  # tpyc: ok
    b, j = mkl(1)
    b = ol
    ol.n += 1
    print("compare", "unpack_lt", Cl(5) < b, b < Cl(6), j)  # tpyc: ok
    if (w := mke1(1)).n > 0:
        w = oe
        oe.n += 1
        print("compare", "walrus_eq", w == Ce(7), w != oe)  # tpyc: ok
    if (v := mkl1(1)).n > 0:
        v = ol
        ol.n += 1
        print("compare", "walrus_lt", Cl(6) < v, v < Cl(7))  # tpyc: ok
    for p in esteps():
        p = oe
        oe.n += 1
        print("compare", "for_own_eq", p == Ce(8), p != oe)  # tpyc: ok
    for q in lsteps_cl():
        q = ol
        ol.n += 1
        print("compare", "for_own_lt", Cl(7) < q, q < Cl(8))  # tpyc: ok
        # A chained compare over the rebound target.
        print("compare", "chained", Cl(7) < q < Cl(9), ol < q < Cl(9))  # tpyc: ok
    with CeCtx() as t:
        t = oe
        oe.n += 1
        print("compare", "with_eq", t == Ce(9), t != oe)  # tpyc: ok
    with ClCtx() as u:
        u = ol
        ol.n += 1
        print("compare", "with_lt", Cl(8) < u, u < Cl(9))  # tpyc: ok
    # The declaration twin.
    x = Ce(1)
    x = oe
    oe.n += 1
    print("compare", "decl_eq", x == Ce(10), x != oe)  # tpyc: ok
    y = Cl(1)
    y = ol
    ol.n += 1
    print("compare", "decl_lt", Cl(9) < y, y < Cl(10))  # tpyc: ok


free_function()
while_head()
while_head_alias_across_passes()
h = Holder()
h.method()
nested()
in_try()
in_match(1)
in_if(True)
in_loop()
try:
    error_return_bind()
except Err:
    print("error_return", "unreachable")
declarations(Outer(3))
inverse()
print("inverse", "own_param", own_param(P(7)).n)
nonlocal_rebind()
with_lent()
loop_escape_hoist()
ref_sink_forms(P(1), [1], bytearray(b"x"))
elif_head(False)
while_last_pass()
while_borrowed([P(1), P(2)])
containers()
for_own_to_lvalue()
print_arg_copy()
take_then_rebind()
take_then_rebind_after()
temp_receiver_rebind(False)
optional_reseat()
optional_reseat_temp_receiver(False)
owning_property_kinds_call()
declaration_rows(P(1))
compare_operands()

# Module level: a for target over an owning iterator.
for mp in steps():
    mq = mp
    mp = P(10)  # tpyc: ok
    mq.n += 10
    print("module", "for_own_rvalue", mq.n, mp.n)
