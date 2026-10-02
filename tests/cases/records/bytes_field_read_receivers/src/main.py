# A `bytes` FIELD read off a CHAINED receiver -- `o.inner.tag`, `rows[0].tag`,
# `d["k"].tag`, `self.o.inner.tag`, three hops -- at every read sink, with the
# `str` twin (`.name`) read beside each subject line as the control. Storage
# rule: a field read of either family, at any depth, takes an owned copy (the
# one view rule), so each section below prints the pre-mutation value CPython
# prints across a write to the field, a mutating method, an alias bind, a
# callee at a mutable parameter, and the write paths no tracker sees (a callee
# writing a global, a `*args` pack, a closure capture, a second name aliasing
# the same container element -- BUGS.md#field-view-escape-needs-place).
from typing import Iterator

from tpy import Own, int32, readonly


class Inner:
    def __init__(self, tag: Own[bytes], name: Own[str]) -> None:
        self.tag = tag
        self.name = name

    def rename(self) -> None:
        self.name = "n6"


class Mid:
    def __init__(self, inner: Own[Inner]) -> None:
        self.inner = inner


class Outer:
    def __init__(self, inner: Own[Inner], mid: Own[Mid]) -> None:
        self.inner = inner
        self.mid = mid


def mk() -> Own[Outer]:
    return Outer(Inner(b"t1", "n1"), Mid(Inner(b"t2", "n2")))


def take(v: bytes) -> int32:
    return len(v)


# free function: every receiver rung at the decl sink, the str twin beside it
def sec_free() -> None:
    o = mk()
    rows = [Inner(b"e1", "m1")]
    d = {"k": Inner(b"d1", "p1")}
    tg = o.inner.tag  # tpyc: ok
    el = rows[0].tag  # tpyc: ok
    dv = d["k"].tag  # tpyc: ok
    hp = o.mid.inner.tag  # tpyc: ok
    # the `str` twin copies at this depth too -- the family does not decide it
    nm = o.inner.name  # tpyc: type(str)
    print("free", tg, el, dv, hp, nm)
    print("free sinks", o.inner.tag == b"t1", take(rows[0].tag))  # tpyc: ok
    # the INSERT slot: the owned element copies the member read
    acc: list[bytes] = []
    acc.append(o.inner.tag)  # tpyc: ok
    acc.insert(0, rows[0].tag)  # tpyc: ok
    o.inner.tag = b"Z1"
    print("free copy", o.inner.tag, tg, acc[0], acc[1])


# the CHAIN's own mutate-after: writing the leaf through the same three hops
# demotes the deep read to an owned copy, so the pre-write value survives
def sec_chain_write() -> None:
    o = mk()
    hp = o.mid.inner.tag  # tpyc: ok
    nm = o.mid.inner.name
    o.mid.inner.tag = b"Z2"
    o.mid.inner.name = "n3"
    print("chain write", hp, nm, o.mid.inner.tag, o.mid.inner.name)


def zap(i: Inner) -> None:
    i.tag = b"Z3"
    i.name = "n4"


# the ROOT record is the argument: `zap(o.mid.inner)`, a record argument
# spelled as a two-hop field chain, is rejected (BUGS.md#deep-field-chain-at-record-arg)
def zap_deep(x: Outer) -> None:
    x.inner.tag = b"Z6"


def peek(x: Outer) -> int32:
    return take(x.inner.tag)


def peek_ro(x: readonly[Outer]) -> int32:
    return take(x.inner.tag)


def peek_ro_one(i: readonly[Inner]) -> int32:
    return take(i.tag)


# ESCAPE into a callee: a record at a mutable parameter can have any field
# under it replaced; both one-hop reads are copies taken before the call
def sec_callee(one: Inner, o: Outer, o2: Outer, o3: Outer) -> None:
    v = one.tag  # tpyc: ok
    s = one.name  # tpyc: type(str)
    zap(one)
    print("callee root", v, s, one.tag, one.name)
    # the callee writes a field two hops under the argument
    deep = o.inner.tag  # tpyc: ok
    zap_deep(o)
    print("callee deep", deep, o.inner.tag)
    ro = o2.inner.tag  # tpyc: type(bytes)
    print("callee readonly", peek_ro(o2), ro)
    pv = o3.inner.name  # tpyc: type(str)
    print("callee plain", peek(o3), pv)


# the shallowest read there is -- one hop off a parameter. Both families copy;
# the `bytes` copy keeps its value across the write two lines down
def sec_one_hop(i: Inner) -> None:
    s = i.name  # tpyc: type(str)
    t = i.tag  # tpyc: type(bytes)
    i.tag = b"Z7"
    print("one hop", s, t, i.tag, peek_ro_one(i))


GLOB = Inner(b"g1", "g2")


def zapg() -> None:
    GLOB.tag = b"Z8"


# a GLOBAL record read: one hop off the global name, and the local OWNS its
# buffer. `zapg()` replaces the field through no argument and no alias bind
# (BUGS.md#field-view-escape-needs-place); a view here would read freed
# storage, the copy survives and prints CPython's value
def sec_global() -> None:
    v = GLOB.tag  # tpyc: type(bytes)
    zapg()
    print("global", v, GLOB.tag)


# an ALIAS bind alongside a deep field read: the read owns its copy for its
# depth alone, so this section pins the write-through-the-alias output
def sec_alias(o: Outer) -> None:
    v = o.mid.inner.tag  # tpyc: ok
    s = o.mid.inner.name
    m = o.mid
    m.inner.tag = b"Z4"
    m.inner.name = "n5"
    print("alias", v, s, o.mid.inner.tag, o.mid.inner.name)


# the deep read spelled THROUGH the alias, the write through the original:
# one buffer, two spellings -- again an output pin
def sec_alias_rev(o: Outer) -> None:
    m = o.mid
    v = m.inner.tag  # tpyc: ok
    o.mid.inner.tag = b"Z5"
    print("alias rev", v, o.mid.inner.tag)


# the one-hop `str` read with a second name bound to the record and the write
# spelled through THAT name: the local holds the pre-write text
def sec_onehop_alias(i: Inner) -> None:
    v = i.name  # tpyc: type(str)
    m = i
    m.name = "n7"
    print("one hop alias", v, i.name)


# the same buffer reached the other way round: the read is spelled through the
# alias and the write through the original
def sec_onehop_alias_rev(i: Inner) -> None:
    m = i
    v = m.name  # tpyc: type(str)
    i.name = "n8"
    print("one hop alias rev", v, i.name)


# a mutating METHOD replaces the field's buffer; the copy predates the call
def sec_onehop_method(i: Inner) -> None:
    v = i.name  # tpyc: type(str)
    i.rename()
    print("one hop method", v, i.name)


class Hidden:
    def __init__(self, inner: Own[Inner]) -> None:
        self._inner = inner

    @property
    def inner(self) -> Inner:
        return self._inner


# a property hop mints a temporary the dotted path does not own, so the deep
# read owns its copy no matter what follows
def sec_hidden_call(h: Hidden) -> None:
    v = h.inner.tag  # tpyc: type(bytes)
    print("hidden", v)


# a `readonly` ROOT does not buy a view back: a field read copies at any
# depth, and the root's constness never enters the verdict
def sec_readonly_root(o: readonly[Outer]) -> None:
    v = o.inner.tag  # tpyc: type(bytes)
    s = o.inner.name  # tpyc: type(str)
    print("readonly root", v, s)


# method: the chain rooted at `self`, at a decl and at the return sink
class Runner:
    def __init__(self, o: Own[Outer]) -> None:
        self.o = o

    def run(self) -> bytes:
        tg = self.o.inner.tag  # tpyc: ok
        print("method", tg, self.o.mid.inner.tag == b"t2")
        return self.o.inner.tag  # tpyc: ok


# constructor body: the same chained read into a local
class Snap:
    n: int32

    def __init__(self, o: Outer) -> None:
        tg = o.inner.tag  # tpyc: ok
        self.n = take(tg)
        print("ctor", tg, o.mid.inner.tag)


# generator: the frame's own decl slot takes the chained read
def sec_gen(o: Outer) -> Iterator[int32]:
    tg = o.inner.tag  # tpyc: ok
    yield take(tg)
    print("gen", tg, o.inner.tag == b"t1")


class Gate:
    hits: int32

    def __init__(self) -> None:
        self.hits = 0

    def __enter__(self) -> "Gate":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.hits += 1


# with body / try-finally / match arm, and the closure that reads the chain
def sec_blocks(o: Outer) -> None:
    with Gate() as g:
        tg = o.inner.tag  # tpyc: ok
        g.hits += 1
    print("with", tg)
    try:
        ft = o.mid.inner.tag  # tpyc: ok
    finally:
        print("try", ft)
    k = take(o.inner.tag)
    match k:
        case 2:
            print("match", o.inner.tag)  # tpyc: ok
        case _:
            print("match other")

    def inner_read() -> int32:
        return take(o.inner.tag)  # tpyc: ok

    print("closure", inner_read())


# comprehension: the chained read inside the element expression
def sec_comp(rows: list[Outer]) -> None:
    lens = [take(r.inner.tag) for r in rows]  # tpyc: ok
    print("comp", lens)


def main() -> None:
    sec_free()
    sec_chain_write()
    sec_callee(Inner(b"c1", "q1"), mk(), mk(), mk())
    sec_one_hop(Inner(b"o1", "s1"))
    sec_global()
    sec_alias(mk())
    sec_alias_rev(mk())
    sec_onehop_alias(Inner(b"a1", "u1"))
    sec_onehop_alias_rev(Inner(b"a2", "u2"))
    sec_onehop_method(Inner(b"a3", "u3"))
    sec_hidden_call(Hidden(Inner(b"h1", "r1")))
    ro_src = mk()
    sec_readonly_root(ro_src)
    # the callee prints, so bind before printing (BUGS.md#print-arg-output-interleaves)
    got = Runner(mk()).run()
    print("method ret", got)
    Snap(mk())
    o = mk()
    for v in sec_gen(o):
        print("gen yield", v)
    sec_blocks(mk())
    sec_comp([mk(), mk()])


main()
