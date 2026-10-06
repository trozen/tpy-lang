# dict.get(key, default) over reference-type values hands back the stored
# value or the default ITSELF (CPython aliases both); a temporary default is
# read in place and copied only where a binding holds it (warned there). An
# owned element slot (tuple, literal, comprehension, an empty container's
# first store) holds a warned copy.
import asyncio
from typing import Callable, Iterator
from tpy import copy, int32, readonly, Own


class P:
    def __init__(self, n: int32) -> None:
        self.n = n


# free function: a hit borrows the stored value, a miss the default
def hit_miss() -> None:
    d = {"a": P(1)}
    fb = P(9)
    m = d.get("a", fb)  # tpyc: ok
    m.n += 1
    q = d.get("zz", fb)  # tpyc: ok
    q.n += 1
    print("hit_miss", d["a"].n, fb.n)


# readonly receiver: the result is read-only (`const P&`)
def ro(d: readonly[dict[str, P]], fb: P) -> int32:
    r = d.get("a", fb)  # tpyc: ok type(/readonly/)
    return r.n


# temporary default: read in place silently; a local holding it is a copy
# (warned), read only here since CPython would alias the stored value
def temp_default() -> None:
    d = {"a": P(1)}
    print("temp", d.get("zz", P(5)).n)  # tpyc: ok
    m = d.get("a", P(5))  # tpyc: warning(/copies P into local 'm'/)
    print("temp_bound", m.n)
    # two gets over one dict in one statement: a container's element is not
    # an iterator step, so the second call does not invalidate the first
    t = (d.get("a", P(6)).n, d.get("zz", P(7)).n)  # tpyc: ok
    print("temp_twice", t[0], t[1])


class H:
    d: dict[str, P]
    fb: P

    def __init__(self) -> None:
        self.d = {"k": P(3)}
        self.fb = P(7)

    # method: the receiver's field lends
    def bump(self) -> None:
        v = self.d.get("k", self.fb)  # tpyc: ok
        v.n += 1


# generator frame: the borrow is held across a yield
def gen(d: dict[str, P], fb: P) -> Iterator[int32]:
    v = d.get("a", fb)  # tpyc: ok
    yield v.n
    v.n += 1
    yield v.n
    # a walrus in a frame condition holds the same alias: the stored value
    # on a hit, the default itself on a miss
    if (w := d.get("a", fb)).n > 0:  # tpyc: ok
        w.n += 1
    if (u := d.get("zz", fb)).n > 0:  # tpyc: ok
        u.n += 1


# async frame: the borrow is held in the coroutine
async def co(d: dict[str, P], fb: P) -> int32:
    v = d.get("a", fb)  # tpyc: ok
    v.n += 1
    if (w := d.get("a", fb)).n > 0:  # tpyc: ok
        await asyncio.sleep(0)
        w.n += 1
    if (u := d.get("zz", fb)).n > 0:  # tpyc: ok
        await asyncio.sleep(0)
        u.n += 1
    return v.n


# comprehension: read in place per element
def comp(d: dict[str, P], fb: P) -> None:
    print("comp", [d.get(k, fb).n for k in ["a", "zz"]])  # tpyc: ok


# a borrow hoisted across if/else and try/finally: mutable, as at one site
def branches(d: dict[str, P], fb: P, c: bool) -> None:
    if c:
        m = d.get("a", fb)  # tpyc: ok
    else:
        m = d.get("zz", fb)  # tpyc: ok
    m.n += 1
    try:
        t = d.get("zz", fb)  # tpyc: ok
        t.n += 10
    finally:
        print("branches", d["a"].n, fb.n)


# copy() acknowledges the copy: silent, and writes stay off the dict
def explicit_copy(d: dict[str, P], fb: P) -> None:
    c = copy(d.get("a", fb))  # tpyc: ok
    c.n += 100
    print("copy", c.n, d["a"].n)


class Q(P):
    pass


# a derived default at a base-typed dict is the default itself
def derived_default(d: dict[str, P]) -> None:
    q = Q(4)
    m = d.get("zz", q)  # tpyc: ok
    m.n += 1
    print("derived", q.n)


# return position: the result borrows the dict and the default
def ret(d: dict[str, P], k: str, dflt: P) -> P:
    return d.get(k, dflt)  # tpyc: ok


# field store copies, warned (as the free form does); read only here,
# since CPython would alias the stored value
class Holder:
    def __init__(self, p: P) -> None:
        self.p = p  # tpyc: warning(/copies P into field/)


def field_store(d: dict[str, P], fb: P) -> None:
    h = Holder(P(0))
    h.p = d.get("a", fb)  # tpyc: warning(/copies P into field/)
    print("field", h.p.n)


# list values: the stored list or the default list itself
def list_values() -> None:
    d: dict[str, list[int32]] = {"a": [1]}
    fb: list[int32] = []
    d.get("a", fb).append(2)  # tpyc: ok
    d.get("zz", fb).append(3)  # tpyc: ok
    print("list", len(d["a"]), len(fb))


# generic body: the result is a copy at every V (a value V as before; a
# class V copied, warned), so it returns as Own[V]; read only, since CPython
# would alias the stored value
def generic[K, V](d: dict[K, V], k: K, dflt: V) -> Own[V]:
    return d.get(k, dflt)  # tpyc: warning(/may copy V into owned storage/)


def generic_use(d: dict[str, P], fb: P) -> None:
    c = {"a": 1}
    print("generic", generic(c, "a", 0), generic(c, "z", 7),
          generic(d, "zz", fb).n)


# generic body over `V | None` values: the result is the by-value optional,
# a copy at every V (warned); read only, since CPython would alias it. The
# default is the stored entry under `fk`: an `int32 | None` argument at the
# generic call is `BUGS.md#optional-value-local-at-generic-slot`
def generic_opt[V](d: dict[str, V | None], k: str, fk: str,
                   read: Callable[[V], int32]) -> int32:
    dflt = d[fk]
    m = d.get("q", dflt)  # tpyc: warning(/may copy V \| None into local 'm'/)
    # reseated: the second by-value optional replaces the first
    m = d.get(k, dflt)  # tpyc: warning(/may copy V \| None into local 'm'/)
    # a `None` literal default is the empty optional
    w = d.get("zz", None)  # tpyc: warning(/may copy V \| None into local 'w'/)
    if w is not None:
        return -2
    if m is None:
        return -1
    return read(m)


def read_int(v: int32) -> int32:
    return v


def read_p(p: P) -> int32:
    return p.n


def generic_opt_use() -> None:
    c: dict[str, int32 | None] = {"a": 1, "n": None, "s": 7}
    print("generic_opt int", generic_opt(c, "a", "n", read_int),
          generic_opt(c, "z", "s", read_int), generic_opt(c, "z", "n", read_int))
    po: dict[str, P | None] = {"a": P(4), "n": None, "s": P(6)}
    print("generic_opt P", generic_opt(po, "a", "n", read_p),
          generic_opt(po, "z", "s", read_p), generic_opt(po, "z", "n", read_p))


# value and str values: unchanged by-value results
def values() -> None:
    c = {"a": 1}
    print("value", c.get("a", 7), c.get("z", 7))  # tpyc: ok
    s = {"a": "x"}
    print("str", s.get("a", "zz"), s.get("z", "zz"))  # tpyc: ok
    # a bound value result is the caller's own: a later write stays off c
    n = c.get("a", 7)  # tpyc: ok
    n += 1
    print("value_bound", n, c["a"])


# walrus: the bound name is the stored value (hit) or the default (miss)
def walrus(d: dict[str, P], fb: P) -> None:
    if (w := d.get("a", fb)).n > 0:  # tpyc: ok
        w.n += 1
    if (u := d.get("zz", fb)).n > 0:  # tpyc: ok
        u.n += 1
    print("walrus", d["a"].n, fb.n)


# try/finally hoist of a HIT: the stored value itself
def try_hit(d: dict[str, P], fb: P) -> None:
    try:
        t = d.get("a", fb)  # tpyc: ok
        t.n += 1
    finally:
        print("try_hit", d["a"].n)


# readonly receiver at the hoists and in frames: the result stays read-only;
# the hoists only read (the mutable `branches` section observes the alias),
# the generator observes a write the caller makes between two steps
def ro_branches(d: readonly[dict[str, P]], fb: P, c: bool) -> int32:
    if c:
        m = d.get("a", fb)  # tpyc: type(/readonly/)
    else:
        m = d.get("zz", fb)  # tpyc: type(/readonly/)
    try:
        t = d.get("zz", fb)  # tpyc: type(/readonly/)
    finally:
        pass
    return m.n + t.n


def ro_gen(d: readonly[dict[str, P]], fb: P) -> Iterator[int32]:
    v = d.get("a", fb)  # tpyc: type(/readonly/)
    yield v.n
    yield v.n
    # a frame-condition walrus off a readonly receiver is read-only too (the
    # type annotation cannot name a walrus target, so it reads a local of it)
    if (w := d.get("a", fb)).n > 0:  # tpyc: ok
        r = w  # tpyc: type(/readonly/)
        yield r.n


# only reads: a caller write between two resumptions needs another task to
# run at `await asyncio.sleep(0)`, which TPy's zero sleep does not yield to
# yet (the BUGS.md `asyncio.sleep(0)` entry)
async def ro_co(d: readonly[dict[str, P]], fb: P) -> int32:
    v = d.get("a", fb)  # tpyc: type(/readonly/)
    await asyncio.sleep(0)
    return v.n


class C:
    def __init__(self, n: int32) -> None:
        self.n = n


class R:
    c: C

    def __init__(self, n: int32) -> None:
        self.c = C(n)


# a class field read off the result: the stored value's field itself
def field_hop(d: dict[str, R], fb: R) -> None:
    x = d.get("a", fb).c  # tpyc: ok
    x.n += 1
    y = d.get("zz", fb).c  # tpyc: ok
    y.n += 1
    print("field_hop", d["a"].c.n, fb.c.n)


class K:
    def __init__(self, n: int32) -> None:
        self.n = n

    def __hash__(self) -> int:
        return hash(self.n)

    def __eq__(self, o: "K") -> bool:
        return self.n == o.n


# Owned element slots: each holds a warned copy of the stored value or the
# default, as master did; the sections only read through the element, since
# CPython would alias it.
def owned_elements(d: dict[str, P], fb: P) -> None:
    # tuple literal: owned slot, a warned copy, as master
    t = (d.get("a", fb), 1)  # tpyc: warning(/copies P into owned storage \(tuple element 0\)/)
    print("own_tuple", t[0].n, t[1])
    # list literal: owned slot, a warned copy, as master
    xs = [d.get("zz", fb)]  # tpyc: warning(/copies P into owned storage/)
    print("own_list", xs[0].n)
    # dict literal value: owned slot, a warned copy, as master
    dl = {"q": d.get("a", fb)}  # tpyc: warning(/copies P into owned storage/)
    print("own_dict", dl["q"].n)
    # list comprehension: owned slot, a warned copy, as master
    lc = [d.get(k, fb) for k in ["a", "zz"]]  # tpyc: warning(/copies P into owned storage/)
    print("own_comp", lc[0].n, lc[1].n)
    # dict-comprehension value with a temporary default: owned slot, a warned
    # copy, as master -- taken inside the element's own statement, before the
    # temporary dies
    dc = {k: d.get(k, P(0)) for k in ["a", "zz"]}  # tpyc: warning(/copies P into owned storage/)
    print("own_dcomp", dc["a"].n, dc["zz"].n)
    # dict item store: owned slot, a warned copy, as the free twin
    st: dict[str, P] = {}
    st["k"] = d.get("zz", fb)  # tpyc: warning(/copies P into container/)
    print("own_setitem", st["k"].n)
    # tuple member of a list literal / dict literal value: owned slot, a
    # warned copy, as master
    tl = [(d.get("a", fb), 1)]  # tpyc: warning(/copies P into owned storage \(tuple element 0\)/)
    td = {"q": (d.get("zz", fb), 2)}  # tpyc: warning(/copies P into owned storage \(tuple element 0\)/)
    t0 = tl[0]
    t1 = td["q"]
    print("own_tuple_member", t0[0].n, t0[1], t1[0].n, t1[1])
    # copy() acknowledges the same copy: silent
    ack = [copy(d.get(k, fb)) for k in ["a", "zz"]]  # tpyc: ok
    print("own_ack", ack[0].n, ack[1].n)


# Owned element slots over a list-valued dict: the element holds a warned
# copy of the stored list or the default list, as master; read only, since
# CPython would alias it.
def owned_list_values() -> None:
    dl: dict[str, list[int32]] = {"a": [1]}
    lfb: list[int32] = [7, 8]
    # list literal: owned slot, a warned copy, as master
    ll = [dl.get("a", lfb)]  # tpyc: warning(/copies list\[int32\] into owned storage/)
    print("own_lv_list", len(ll[0]))
    # list comprehension: owned slot, a warned copy, as master
    lc = [dl.get(k, lfb) for k in ["a", "zz"]]  # tpyc: warning(/copies list\[int32\] into owned storage/)
    print("own_lv_comp", len(lc[0]), len(lc[1]))
    # dict-comprehension value: owned slot, a warned copy, as master
    dc = {k: dl.get(k, lfb) for k in ["a", "zz"]}  # tpyc: warning(/copies list\[int32\] into owned storage/)
    print("own_lv_dcomp", len(dc["a"]), len(dc["zz"]))
    # dict item store: owned slot, a warned copy, as master
    st: dict[str, list[int32]] = {}
    st["k"] = dl.get("zz", lfb)  # tpyc: warning(/copies list\[int32\] into container/)
    print("own_lv_setitem", len(st["k"]))


# an Optional element slot: owned, a warned copy, as master; read only
def owned_optional(d: dict[str, P], fb: P) -> None:
    xs: list[P | None] = [d.get("a", fb), None]  # tpyc: warning(/copies P into owned storage/)
    x = xs[0]
    if x is not None:
        print("own_opt", x.n, xs[1] is None)


# a reseated tuple local over BOUND results (the spelling the in-place
# `t = (d.get(..), 1)` refusal asks for): it refers to the stored value, then
# to the default, and a write through the second element reaches fb
def reseated_tuple(d: dict[str, P], fb: P) -> None:
    x = d.get("a", fb)  # tpyc: ok
    t = (x, 1)
    print("reseat_hit", t[0].n, t[1])
    y = d.get("zz", fb)  # tpyc: ok
    t = (y, 0)  # tpyc: ok
    t[0].n += 100
    print("reseat_miss", t[0].n, t[1], fb.n)


# set literal: owned slot, a warned copy, as master
def owned_set() -> None:
    kd = {"a": K(1)}
    kfb = K(2)
    s = {kd.get("a", kfb), kd.get("zz", kfb)}  # tpyc: warning(/copies K into owned storage/)
    print("own_set", len(s))


class W:
    n: int32
    pad: list[int32]

    def __init__(self, n: int32) -> None:
        self.n = n
        self.pad = [n, n, n, n]


# Empty containers take their element type from the first store: an owned
# slot, never a reference to what the call handed back. Each store is a
# warned copy, as master; read only, since CPython would alias it.
def owned_empty(d: dict[str, P], fb: P, wd: dict[str, W]) -> None:
    # first item store of a TEMPORARY default: the dict holds a copy, still
    # intact after an unrelated allocation reuses the temporary's storage
    e = {}
    e["k"] = wd.get("zz", W(3))  # tpyc: warning(/copies W into container/)
    junk = [W(77), W(88)]
    print("own_empty_temp", e["k"].n, e["k"].pad, len(junk))
    # a second store after the first, of a fresh value and over the first key
    e["j"] = W(4)  # tpyc: ok
    e["k"] = W(7)  # tpyc: ok
    print("own_empty_second", e["k"].n, e["j"].n)
    # one store of a stored value or a named default: a copy, as the warning says
    h = {}
    h["k"] = d.get("a", fb)  # tpyc: warning(/copies P into container/)
    print("own_empty_hit", h["k"].n)
    # empty list + append
    xs = []
    xs.append(d.get("zz", fb))  # tpyc: warning(/copies P into owned storage/)
    print("own_empty_append", xs[0].n)
    # empty set + add
    kd = {"a": K(1)}
    s = set()
    s.add(kd.get("a", K(2)))  # tpyc: warning(/copies K into owned storage/)
    s.add(K(3))
    print("own_empty_add", len(s))


# bound from a temporary default, then stored: the local owns its copy (warned
# once, at the binding) and each store moves it, as any owned local's last use
def bound_then_stored(d: dict[str, P]) -> None:
    a = d.get("zz", P(3))  # tpyc: warning(/copies P into local 'a'/)
    e = {"x": P(0)}
    e["k"] = a  # tpyc: ok
    b = d.get("zz", P(4))  # tpyc: warning(/copies P into local 'b'/)
    xs = [P(0)]
    xs.append(b)  # tpyc: ok
    print("bound_stored", e["k"].n, xs[1].n)


# the same empty-container store in a generator body: the frame's dict holds
# a copy of the temporary default
def owned_empty_gen(wd: dict[str, W]) -> Iterator[int32]:
    e = {}
    e["k"] = wd.get("zz", W(5))  # tpyc: warning(/copies W into container/)
    junk = [W(77), W(88)]
    yield e["k"].n + len(junk)
    yield e["k"].pad[3]


# a comprehension in a generator body: owned slot, a warned copy, as master
def owned_gen(d: dict[str, P], fb: P) -> Iterator[int32]:
    xs = [d.get(k, fb) for k in ["a", "zz"]]  # tpyc: warning(/copies P into owned storage/)
    yield xs[0].n
    yield xs[1].n


# readonly sources: a container owns its copy, so the element is writable
# and the readonly source is untouched (the write goes to the copy)
def ro_copy(d: readonly[dict[str, P]], a: readonly[P]) -> None:
    xs = []
    xs.append(d.get("zz", P(4)))  # tpyc: warning(/copies .*P.* into owned storage/)
    xs[0].n = 9
    ys = [a]  # tpyc: warning(/copies readonly\[P\] into owned storage/)
    ys[0].n = 8
    print("ro_copy", xs[0].n, ys[0].n)


def main() -> None:
    hit_miss()
    d = {"a": P(1)}
    fb = P(9)
    print("ro", ro(d, fb))
    temp_default()
    h = H()
    h.bump()
    print("method", h.d["k"].n)
    steps: list[int32] = []
    for x in gen(d, fb):
        steps.append(x)
    print("gen", steps[0], steps[1], d["a"].n, fb.n)
    print("async", asyncio.run(co(d, fb)), d["a"].n, fb.n)
    comp(d, fb)
    branches(d, fb, True)
    branches(d, fb, False)
    explicit_copy(d, fb)
    derived_default(d)
    r1 = ret(d, "a", fb)
    r1.n += 1
    r2 = ret(d, "zz", fb)
    r2.n += 1
    print("ret", d["a"].n, fb.n)
    field_store(d, fb)
    list_values()
    generic_use(d, fb)
    generic_opt_use()
    values()
    walrus(d, fb)
    try_hit(d, fb)
    print("ro_branches", ro_branches(d, fb, True), ro_branches(d, fb, False))
    ro_steps: list[int32] = []
    for step in ro_gen(d, fb):
        ro_steps.append(step)
        d["a"].n += 1
    print("ro_gen", ro_steps[0], ro_steps[1], ro_steps[2])
    print("ro_co", asyncio.run(ro_co(d, fb)))
    rd = {"a": R(1)}
    rf = R(2)
    field_hop(rd, rf)
    owned_elements(d, fb)
    owned_list_values()
    owned_optional(d, fb)
    reseated_tuple(d, fb)
    owned_set()
    for n in owned_gen(d, fb):
        print("own_gen", n)
    wd = {"a": W(1)}
    owned_empty(d, fb, wd)
    bound_then_stored(d)
    for n in owned_empty_gen(wd):
        print("own_empty_gen", n)
    ro_copy(d, fb)


main()
