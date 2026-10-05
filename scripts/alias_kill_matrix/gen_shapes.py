"""The semantic rows of the acceptance matrix, generated per run
(`run_matrix.py` writes them under build/; they are never committed: they
are the output of this file). Cost rows come from gen_cost.py.

Run: python gen_shapes.py [out_dir]   (default: build/shapes next to this file)
"""
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "build" / "shapes"

PRE_NM = '''from tpy import Own


class M:
    v: int | None
    w: int | None

    def __init__(self) -> None:
        self.v = 1
        self.w = 1

    def reset(self) -> None:
        self.v = None


class N:
    v: int | None
    w: int | None
    inner: M

    def __init__(self) -> None:
        self.v = 1
        self.w = 1
        self.inner = M()

    def reset(self) -> None:
        self.v = None


def clear(n: N) -> None:
    n.v = None


def clear_m(m: M) -> None:
    m.v = None


def none_m() -> Own[M]:
    m = M()
    m.v = None
    return m
'''

PRE_NM_BOOL = '''from tpy import Own


class N:
    v: int | None
    ok: bool

    def __init__(self) -> None:
        self.v = 1
        self.ok = True

    def __bool__(self) -> bool:
        return self.ok


def falsy() -> Own[N]:
    n = N()
    n.ok = False
    return n
'''

PRE_DEEP = '''
class E:
    v: int | None

    def __init__(self) -> None:
        self.v = 1


class D:
    e: E
    e2: E

    def __init__(self) -> None:
        self.e = E()
        self.e2 = E()


class C:
    d: D

    def __init__(self) -> None:
        self.d = D()


class B:
    c: C

    def __init__(self) -> None:
        self.c = C()


class A:
    b: B
    x: B

    def __init__(self) -> None:
        self.b = B()
        self.x = B()
'''

PRE_L = '''from tpy import Ptr, int32


class L:
    v: int | None
    n: int32
    next: Ptr["L"]

    def __init__(self, nxt: Ptr["L"]) -> None:
        self.v = 1
        self.n = 0
        self.next = nxt

    def reset(self) -> None:
        self.v = None
'''

ROWS: list[tuple[str, str, str, list[str], str, str, str]] = []


def row(name: str, group: str, expected: str, desc: str, pre: str, body: str,
        notes: list[str] | None = None, verdict: str = "local") -> None:
    ROWS.append((name, group, expected, notes or [], verdict, desc, pre + "\n" + body))


I, P, U, K = "inline", "pointer", "unmodelled", "meet"
CK, UN = "CHECKED", "UNCHECKED"

# ---------------------------------------------------------------- inline
row("inl_ternary", I, CK, "ternary alias store kills a.v", PRE_NM, '''
def f(a: N, c: bool) -> None:
    t = a if c else N()
    if a.v is not None:
        t.v = None
        y = a.v  # SUBJECT
        print("Y", y)


def main() -> None:
    f(N(), True)
''')

row("inl_ternary_kept", I, UN, "ternary over an unrelated param and a fresh object keeps a.v", PRE_NM, '''
def f(a: N, b: N, c: bool) -> None:
    t = b if c else N()
    if a.v is not None:
        t.v = None
        y = a.v  # SUBJECT
        print("Y", y)


def main() -> None:
    f(N(), N(), True)
''', ["distinct parameters are distinct objects under the model (f(n, n) is the filed exception)"])

row("inl_or", I, CK, "`b or a` alias store kills a.v", PRE_NM_BOOL, '''
def f(a: N, b: N) -> None:
    t = b or a
    if a.v is not None:
        t.v = None
        y = a.v  # SUBJECT
        print("Y", y)


def main() -> None:
    f(N(), falsy())
''', ["N defines __bool__ so CPython can take the `a` arm"])

row("inl_or_kept", I, UN, "`b or N()` keeps a.v", PRE_NM_BOOL, '''
def f(a: N, b: N) -> None:
    t = b or N()
    if a.v is not None:
        t.v = None
        y = a.v  # SUBJECT
        print("Y", y)


def main() -> None:
    f(N(), falsy())
''')

row("inl_walrus", I, CK, "walrus alias store kills a.v", PRE_NM, '''
def f(a: N) -> None:
    print("w", (t := a).w)
    if a.v is not None:
        t.v = None
        y = a.v  # SUBJECT
        print("Y", y)


def main() -> None:
    f(N())
''')

row("inl_walrus_kept", I, UN, "walrus of a fresh object keeps a.v", PRE_NM, '''
def f(a: N) -> None:
    print("w", (t := N()).w)
    if a.v is not None:
        t.v = None
        y = a.v  # SUBJECT
        print("Y", y)


def main() -> None:
    f(N())
''')

row("inl_unpack", I, CK, "tuple-unpack alias store kills a.v", PRE_NM, '''
def f(a: N) -> None:
    t, u = a, N()
    if a.v is not None:
        t.v = None
        y = a.v  # SUBJECT
        print("Y", y, u.w)


def main() -> None:
    f(N())
''')

row("inl_unpack_kept", I, UN, "store through the other unpacked name keeps a.v", PRE_NM, '''
def f(a: N) -> None:
    t, u = a, N()
    if a.v is not None:
        u.v = None
        y = a.v  # SUBJECT
        print("Y", y, t.w)


def main() -> None:
    f(N())
''')

row("inl_alias_chain", I, CK, "store through t3 = t2 = t1 = a kills a.v", PRE_NM, '''
def f(a: N) -> None:
    t1 = a
    t2 = t1
    t3 = t2
    if a.v is not None:
        t3.v = None
        y = a.v  # SUBJECT
        print("Y", y)


def main() -> None:
    f(N())
''')

row("inl_alias_chain_kept", I, UN, "alias chain from a fresh object keeps a.v", PRE_NM, '''
def f(a: N) -> None:
    t1 = N()
    t2 = t1
    t3 = t2
    if a.v is not None:
        t3.v = None
        y = a.v  # SUBJECT
        print("Y", y)


def main() -> None:
    f(N())
''')

row("inl_field_chain", I, CK, "t = a.inner; t.v = None kills a.inner.v", PRE_NM, '''
def f(a: N) -> None:
    t = a.inner
    if a.inner.v is not None:
        t.v = None
        y = a.inner.v  # SUBJECT
        print("Y", y)


def main() -> None:
    f(N())
''')

row("inl_field_chain_sibling", I, UN, "t = a.inner; t.v = None keeps a.v", PRE_NM, '''
def f(a: N) -> None:
    t = a.inner
    if a.v is not None:
        t.v = None
        y = a.v  # SUBJECT
        print("Y", y)


def main() -> None:
    f(N())
''')

row("inl_field_chain_otherfield", I, UN, "t = a.inner; t.v = None keeps a.inner.w", PRE_NM, '''
def f(a: N) -> None:
    t = a.inner
    if a.inner.w is not None:
        t.v = None
        y = a.inner.w  # SUBJECT
        print("Y", y)


def main() -> None:
    f(N())
''')

row("inl_composed_fwd", I, CK, "b = a; t = b.inner; a.inner.v store kills t.v", PRE_NM, '''
def f(a: N, opt: int | None) -> None:
    b = a
    t = b.inner
    if t.v is not None:
        a.inner.v = opt
        y = t.v  # SUBJECT
        print("Y", y)


def main() -> None:
    f(N(), None)
''', ["two-level literal None store rejects (assign.field_write_shape); stores an Optional parameter holding None"])

row("inl_composed_rev", I, CK, "b = a; t = b.inner; t.v = None kills a.inner.v", PRE_NM, '''
def f(a: N) -> None:
    b = a
    t = b.inner
    if a.inner.v is not None:
        t.v = None
        y = a.inner.v  # SUBJECT
        print("Y", y)


def main() -> None:
    f(N())
''')

row("inl_reassigned_store", I, CK, "h2 = h store, h2 rebound later, kills h.v", PRE_NM, '''
def f(h: N, other: N) -> None:
    h2 = h
    if h.v is not None:
        h2.v = None
        y = h.v  # SUBJECT
        print("Y", y)
    h2 = other
    print(h2.w, h.w)


def main() -> None:
    f(N(), N())
''')

row("inl_reassigned_call", I, CK, "h2 = h; h2.reset(), h2 rebound later, kills h.v", PRE_NM, '''
def f(h: N, other: N) -> None:
    h2 = h
    if h.v is not None:
        h2.reset()
        y = h.v  # SUBJECT
        print("Y", y)
    h2 = other
    print(h2.w, h.w)


def main() -> None:
    f(N(), N())
''')

row("inl_store_optional_alias", I, CK, "alias store of a possibly-None parameter kills a.v", PRE_NM, '''
def f(a: N, c: bool, opt: int | None) -> None:
    t = a if c else N()
    if a.v is not None:
        t.v = opt
        y = a.v  # SUBJECT
        print("Y", y)


def main() -> None:
    f(N(), True, None)
''')

row("inl_store_optional_direct", I, CK, "direct store of a possibly-None parameter kills a.v", PRE_NM, '''
def f(a: N, opt: int | None) -> None:
    if a.v is not None:
        a.v = opt
        y = a.v  # SUBJECT
        print("Y", y)


def main() -> None:
    f(N(), None)
''')

row("inl_store_nonnone_direct", I, UN, "direct non-None store keeps a.w", PRE_NM, '''
def f(a: N) -> None:
    if a.w is not None:
        a.w = 5
        y = a.w  # SUBJECT
        print("Y", y)


def main() -> None:
    f(N())
''')

row("inl_store_nonnone_name", I, UN, "direct store of a non-Optional name keeps a.w", PRE_NM, '''
def f(a: N, k: int) -> None:
    if a.w is not None:
        a.w = k
        y = a.w  # SUBJECT
        print("Y", y)


def main() -> None:
    f(N(), 4)
''')

row("inl_store_nonnone_alias", I, UN, "alias store of a non-None value, fact on a.v", PRE_NM, '''
def f(a: N) -> None:
    t = a
    if a.v is not None:
        t.v = 5
        y = a.v  # SUBJECT
        print("Y", y)


def main() -> None:
    f(N())
''', ["the model's store rule is value-blind (kills by slot); a value-aware rule would keep it -- truth stays-value either way"])

row("inl_unrelated", I, UN, "store through a fresh local keeps a.v", PRE_NM, '''
def f(a: N) -> None:
    if a.v is not None:
        u = N()
        u.v = None
        y = a.v  # SUBJECT
        print("Y", y)


def main() -> None:
    f(N())
''')

row("inl_plain_bind_nostore", I, UN, "t = a bound inside the narrowing, no store", PRE_NM, '''
def f(a: N) -> None:
    if a.v is not None:
        t = a
        y = a.v  # SUBJECT
        print("Y", y, t.w)


def main() -> None:
    f(N())
''', ["model kills on stores and calls only; branch-head kills at the bind by design (committed case plain_bind)"])

def _kchain(depth: int) -> str:
    src = ["", "", "class K%d:" % depth, "    v: int | None", "    w: int | None", "",
           "    def __init__(self) -> None:", "        self.v = 1", "        self.w = 1"]
    for i in range(depth - 1, -1, -1):
        src += ["", "", "class K%d:" % i, "    f: K%d" % (i + 1), "    g: K%d" % (i + 1), "",
                "    def __init__(self) -> None:", "        self.f = K%d()" % (i + 1),
                "        self.g = K%d()" % (i + 1)]
    return "\n".join(src) + "\n"


PRE_K = _kchain(5)


def _binds(var: str, root: str, hops: str) -> str:
    out = ["    %s0 = %s" % (var, root)]
    for i, h in enumerate(hops, 1):
        out.append("    %s%d = %s%d.%s" % (var, i, var, i - 1, h))
    return "\n".join(out) + "\n"


DEEP_NOTE = ["a 2+-field chain in one expression rejects today (decl.slot_type / if.cond_binop.is not); every place is bound one hop at a time"]

row("inl_deep_kill", I, CK, "t5 = a.f.f.f.f.f (5 fields, stepwise); s5 (same place) .v = None kills t5.v", PRE_K, '''
def f(a: K0) -> None:
''' + _binds("t", "a", "fffff") + _binds("s", "a", "fffff") + '''    if t5.v is not None:
        s5.v = None
        y = t5.v  # SUBJECT
        print("Y", y)


def main() -> None:
    f(K0())
''', DEEP_NOTE)

row("inl_deep_sibling", I, UN, "fact on a.f.f.f.f.f.v; store through a.f.f.f.f.g keeps it", PRE_K, '''
def f(a: K0) -> None:
''' + _binds("t", "a", "fffff") + _binds("s", "a", "ffffg") + '''    if t5.v is not None:
        s5.v = None
        y = t5.v  # SUBJECT
        print("Y", y)


def main() -> None:
    f(K0())
''', DEEP_NOTE)

row("inl_unrelated_branch", I, UN, "fact on a.f.f.f.f.f.v; store through a.g.f.f.f.f keeps it", PRE_K, '''
def f(a: K0) -> None:
''' + _binds("t", "a", "fffff") + _binds("s", "a", "gffff") + '''    if t5.v is not None:
        s5.v = None
        y = t5.v  # SUBJECT
        print("Y", y)


def main() -> None:
    f(K0())
''', DEEP_NOTE)

row("inl_deep_otherfield", I, UN, "fact on a.f^5.w; store of a.f^5.v (same object, other field) keeps it", PRE_K, '''
def f(a: K0) -> None:
''' + _binds("t", "a", "fffff") + _binds("s", "a", "fffff") + '''    if t5.w is not None:
        s5.v = None
        y = t5.w  # SUBJECT
        print("Y", y)


def main() -> None:
    f(K0())
''', DEEP_NOTE)

row("inl_deep_call_ancestor", I, CK, "clear_k(a.f.f) (mutating call on an ancestor) kills a.f^5.v", PRE_K + '''

def clear_k(k: K2) -> None:
    k1 = k.f
    k2 = k1.f
    k3 = k2.f
    k3.v = None
''', '''
def f(a: K0) -> None:
''' + _binds("t", "a", "fffff") + _binds("s", "a", "ff") + '''    if t5.v is not None:
        clear_k(s2)
        y = t5.v  # SUBJECT
        print("Y", y)


def main() -> None:
    f(K0())
''', DEEP_NOTE)

row("inl_two_roots", I, CK, "t0 = a if c else x; t5 = t0.f.f.f.f.f (stepwise); t5.v = None kills x.f^5.v", PRE_K, '''
def f(a: K0, x: K0, c: bool) -> None:
    t0 = a if c else x
    t1 = t0.f
    t2 = t1.f
    t3 = t2.f
    t4 = t3.f
    t5 = t4.f
''' + _binds("s", "x", "fffff") + '''    if s5.v is not None:
        t5.v = None
        y = s5.v  # SUBJECT
        print("Y", y)


def main() -> None:
    f(K0(), K0(), False)
''', DEEP_NOTE)


row("inl_loop_entry", I, CK, "store through alias after the read reaches it on the back edge", PRE_NM, '''
def f(a: N, c: bool) -> None:
    t = a if c else N()
    if a.v is not None:
        i = 0
        while i < 2:
            y = a.v  # SUBJECT
            print("Y", y)
            t.v = None
            i += 1


def main() -> None:
    f(N(), True)
''')

row("inl_loop_entry_kept", I, UN, "back-edge store through an unrelated alias keeps a.v", PRE_NM, '''
def f(a: N, b: N, c: bool) -> None:
    t = b if c else N()
    if a.v is not None:
        i = 0
        while i < 2:
            y = a.v  # SUBJECT
            print("Y", y)
            t.v = None
            i += 1


def main() -> None:
    f(N(), N(), True)
''')

row("inl_handler_entry", I, CK, "store through alias before a raise reaches the handler", PRE_NM, '''
def f(a: N, c: bool) -> None:
    t = a if c else N()
    if a.v is not None:
        try:
            t.v = None
            raise ValueError("stop")
        except ValueError:
            y = a.v  # SUBJECT
            print("Y", y)


def main() -> None:
    f(N(), True)
''')

row("inl_handler_entry_kept", I, UN, "handler entry, store through an unrelated alias keeps a.v", PRE_NM, '''
def f(a: N, b: N, c: bool) -> None:
    t = b if c else N()
    if a.v is not None:
        try:
            t.v = None
            raise ValueError("stop")
        except ValueError:
            y = a.v  # SUBJECT
            print("Y", y)


def main() -> None:
    f(N(), N(), True)
''')

row("inl_finally_entry", I, CK, "store through alias in try reaches the finally body", PRE_NM, '''
def f(a: N, c: bool) -> None:
    t = a if c else N()
    if a.v is not None:
        try:
            t.v = None
            raise ValueError("stop")
        except ValueError:
            print("caught")
        finally:
            y = a.v  # SUBJECT
            print("Y", y)


def main() -> None:
    f(N(), True)
''')

row("inl_call_ternary", I, CK, "mutable call argument bound by a ternary kills a.v", PRE_NM, '''
def f(a: N, b: N, c: bool) -> None:
    t = a if c else b
    if a.v is not None:
        clear(t)
        y = a.v  # SUBJECT
        print("Y", y)


def main() -> None:
    f(N(), N(), True)
''')

row("inl_call_kwarg", I, CK, "keyword mutable call argument bound by a ternary kills a.v", PRE_NM, '''
def f(a: N, b: N, c: bool) -> None:
    t = a if c else b
    if a.v is not None:
        clear(n=t)
        y = a.v  # SUBJECT
        print("Y", y)


def main() -> None:
    f(N(), N(), True)
''')

row("inl_call_chain", I, CK, "clear_m(a.inner) kills a.inner.v", PRE_NM, '''
def f(a: N) -> None:
    if a.inner.v is not None:
        clear_m(a.inner)
        y = a.inner.v  # SUBJECT
        print("Y", y)


def main() -> None:
    f(N())
''')

row("inl_call_chain_sibling", I, UN, "clear_m(a.inner) keeps a.v (not reachable from a.inner)", PRE_NM, '''
def f(a: N) -> None:
    if a.v is not None:
        clear_m(a.inner)
        y = a.v  # SUBJECT
        print("Y", y)


def main() -> None:
    f(N())
''')

row("inl_call_alias_chain", I, CK, "t = a.inner; clear_m(t) kills a.inner.v", PRE_NM, '''
def f(a: N) -> None:
    t = a.inner
    if a.inner.v is not None:
        clear_m(t)
        y = a.inner.v  # SUBJECT
        print("Y", y)


def main() -> None:
    f(N())
''')

row("inl_call_unrelated", I, UN, "clear(u) on a fresh local keeps a.v", PRE_NM, '''
def f(a: N) -> None:
    u = N()
    if a.v is not None:
        clear(u)
        y = a.v  # SUBJECT
        print("Y", y)


def main() -> None:
    f(N())
''')

row("inl_receiver_alias", I, CK, "t = a if c else b; t.reset() kills a.v", PRE_NM, '''
def f(a: N, b: N, c: bool) -> None:
    t = a if c else b
    if a.v is not None:
        t.reset()
        y = a.v  # SUBJECT
        print("Y", y)


def main() -> None:
    f(N(), N(), True)
''')

row("inl_receiver_ternary_expr", I, CK, "(a if c else b).reset() kills a.v", PRE_NM, '''
def f(a: N, b: N, c: bool) -> None:
    if a.v is not None:
        (a if c else b).reset()
        y = a.v  # SUBJECT
        print("Y", y)


def main() -> None:
    f(N(), N(), True)
''')

row("inl_receiver_field_chain", I, CK, "a.inner.reset() kills a.inner.v", PRE_NM, '''
def f(a: N) -> None:
    t = a.inner
    if a.inner.v is not None:
        t.reset()
        y = a.inner.v  # SUBJECT
        print("Y", y)


def main() -> None:
    f(N())
''')

row("inl_receiver_field_sibling", I, UN, "t = a.inner; t.reset() keeps a.v", PRE_NM, '''
def f(a: N) -> None:
    t = a.inner
    if a.v is not None:
        t.reset()
        y = a.v  # SUBJECT
        print("Y", y)


def main() -> None:
    f(N())
''')

row("inl_self_alias_store", I, CK, "method: s = self if c else other; s.v = None kills self.v", '''
class N:
    v: int | None
    w: int | None

    def __init__(self) -> None:
        self.v = 1
        self.w = 1

    def self_alias(self, other: "N", c: bool) -> None:
        s = self if c else other
        if self.v is not None:
            s.v = None
            y = self.v  # SUBJECT
            print("Y", y)
''', '''
def main() -> None:
    a = N()
    o = N()
    a.self_alias(o, True)
''')

row("inl_self_alias_call", I, CK, "method: s = self if c else other; s.reset() kills self.v", '''
class N:
    v: int | None
    w: int | None

    def __init__(self) -> None:
        self.v = 1
        self.w = 1

    def reset(self) -> None:
        self.v = None

    def self_alias(self, other: "N", c: bool) -> None:
        s = self if c else other
        if self.v is not None:
            s.reset()
            y = self.v  # SUBJECT
            print("Y", y)
''', '''
def main() -> None:
    a = N()
    o = N()
    a.self_alias(o, True)
''')

row("inl_self_alias_kept", I, UN, "method: s = other; s.v = None keeps self.v", '''
class N:
    v: int | None
    w: int | None

    def __init__(self) -> None:
        self.v = 1
        self.w = 1

    def self_alias(self, other: "N", c: bool) -> None:
        s = other if c else N()
        if self.v is not None:
            s.v = None
            y = self.v  # SUBJECT
            print("Y", y)
''', '''
def main() -> None:
    a = N()
    o = N()
    a.self_alias(o, True)
''')

row("inl_len_range_alias", I, CK, "pop through t = xs if c else ys drops xs's len-derived range", '''from tpy import int32
''', '''
def f(xs: list[int32], ys: list[int32], c: bool) -> None:
    t = xs if c else ys
    for i in range(len(xs)):
        t.pop()
        y = xs[i]  # SUBJECT
        print("Y", y)


def main() -> None:
    f([1, 2], [3, 4, 5], True)
''', ["truth: IndexError in CPython is the range violation"], verdict="bounds")

row("inl_len_range_unrelated", I, UN, "append/pop through an alias of an unrelated local list keeps xs's range", '''from tpy import int32
''', '''
def f(xs: list[int32]) -> None:
    ys = [3]
    t = ys
    for i in range(len(xs)):
        t.append(7)
        t.pop()
        y = xs[i]  # SUBJECT
        print("Y", y)
    print(len(ys))


def main() -> None:
    f([1, 2])
''', verdict="bounds")

row("inl_replace_slot", I, CK, "t = a if c else N(); t.inner = none_m() kills a.inner.v (fact under the replaced slot)", PRE_NM, '''
def f(a: N, c: bool) -> None:
    t = a if c else N()
    if a.inner.v is not None:
        t.inner = none_m()
        y = a.inner.v  # SUBJECT
        print("Y", y)


def main() -> None:
    f(N(), True)
''')

row("inl_replace_slot_alias_fact", I, CK, "u = a.inner; t.inner = none_m() kills u.v (u names the replaced inline slot)", PRE_NM, '''
def f(a: N, c: bool) -> None:
    t = a if c else N()
    u = a.inner
    if u.v is not None:
        t.inner = none_m()
        y = u.v  # SUBJECT
        print("Y", y)


def main() -> None:
    f(N(), True)
''', ["CPython keeps the old M in u (stays-value); TPy stores inline, so u denotes the overwritten storage and reads None -- truth here understates TPy's exposure"])

row("inl_replace_ancestor", I, CK, "t = h if c else g; t.n = fresh kills h.n.inner.v", '''
class M:
    v: int | None

    def __init__(self, v: int | None) -> None:
        self.v = v


class N:
    inner: M

    def __init__(self, v: int | None) -> None:
        self.inner = M(v)


class H:
    n: N

    def __init__(self) -> None:
        self.n = N(1)
''', '''
def f(h: H, g: H, c: bool) -> None:
    t = h if c else g
    hn = h.n
    hi = hn.inner
    if hi.v is not None:
        t.n = N(None)
        y = hi.v  # SUBJECT
        print("Y", y)


def main() -> None:
    f(H(), H(), True)
''', ["`h.n.inner.v is not None` rejects (if.cond_binop.is not); fact spelled through stepwise aliases, so CPython keeps the old objects (stays-value) while TPy's inline storage makes hi denote the overwritten slot (None) -- truth understates TPy's exposure"])

row("inl_closure_own", I, CK, "nested def: alias bind, narrowing and store all inside k()", PRE_NM, '''
def f(a: N, c: bool) -> None:
    def k() -> None:
        t = a if c else N()
        if a.v is not None:
            t.v = None
            y = a.v  # SUBJECT
            print("Y", y)
    k()


def main() -> None:
    f(N(), True)
''')

row("inl_closure_own_kept", I, UN, "nested def: own binding of a fresh object keeps a.v", PRE_NM, '''
def f(a: N) -> None:
    def k() -> None:
        t = N()
        if a.v is not None:
            t.v = None
            y = a.v  # SUBJECT
            print("Y", y)
    k()


def main() -> None:
    f(N())
''')

# ---------------------------------------------------------------- pointer
WALK_MAIN = '''
def main() -> None:
    tail = L(None)
    head = L(tail)
    f(head)
'''

row("ptr_walk_sibling_plus", P, UN, "node = node.next walk, node.n += 1 keeps node.v", PRE_L, '''
def f(head: L) -> None:
    node: Ptr[L] = head
    while node is not None:
        if node.v is not None:
            node.n += 1
            y = node.v  # SUBJECT
            print("Y", y)
        node = node.next
''' + WALK_MAIN)

row("ptr_walk_sibling_plain", P, UN, "node = node.next walk, node.n = node.n + 1 keeps node.v", PRE_L, '''
def f(head: L) -> None:
    node: Ptr[L] = head
    while node is not None:
        if node.v is not None:
            node.n = node.n + 1
            y = node.v  # SUBJECT
            print("Y", y)
        node = node.next
''' + WALK_MAIN)

row("ptr_walk_samefield_other", P, CK, "q = head.next; q.v store inside the walk kills node.v", PRE_L, '''
def f(head: L, opt: int | None) -> None:
    q = head.next
    node: Ptr[L] = head
    while node is not None:
        if node.v is not None:
            if node.n == 5 and q is not None:
                q.v = opt
            y = node.v  # SUBJECT
            print("Y", y)
        node = node.next


def main() -> None:
    tail = L(None)
    tail.n = 5
    head = L(tail)
    f(head, None)
''', ["literal None store through a Ptr local rejects (assign.field_write_shape); stores an Optional parameter holding None"])

row("ptr_walk_receiver", P, CK, "node.reset() in the walk kills node.v", PRE_L, '''
def f(head: L) -> None:
    node: Ptr[L] = head
    while node is not None:
        if node.v is not None:
            node.reset()
            y = node.v  # SUBJECT
            print("Y", y)
        node = node.next
''' + WALK_MAIN)

row("ptr_walk_head_receiver", P, CK, "fact head.v, walk calling node.reset() kills it", PRE_L, '''
def f(head: L) -> None:
    node: Ptr[L] = head
    if head.v is not None:
        while node is not None:
            node.reset()
            node = node.next
        y = head.v  # SUBJECT
        print("Y", y)
''' + WALK_MAIN)

row("ptr_walk_head_store", P, CK, "fact head.v, walk storing node.v kills it", PRE_L, '''
def f(head: L, opt: int | None) -> None:
    node: Ptr[L] = head
    if head.v is not None:
        while node is not None:
            node.v = opt
            node = node.next
        y = head.v  # SUBJECT
        print("Y", y)


def main() -> None:
    tail = L(None)
    head = L(tail)
    f(head, None)
''')

row("ptr_walk_head_sibling", P, UN, "fact head.v, walk storing node.n keeps it", PRE_L, '''
def f(head: L) -> None:
    node: Ptr[L] = head
    if head.v is not None:
        while node is not None:
            node.n += 1
            node = node.next
        y = head.v  # SUBJECT
        print("Y", y)
''' + WALK_MAIN)

row("ptr_ring", P, CK, "two-node ring: a walk from head.next comes back to head; node.reset()", PRE_L, '''
def f(head: L) -> None:
    node = head.next
    i = 0
    while node is not None and i < 3:
        node = node.next
        i += 1
    if head.v is not None:
        if node is not None:
            node.reset()
        y = head.v  # SUBJECT
        print("Y", y)


def main() -> None:
    a = L(None)
    b = L(a)
    a.next = b
    f(a)
''')

row("ptr_hop4_call", P, CK, "n = head.next.next.next.next; n.reset() kills head.v", PRE_L, '''
def f(head: L) -> None:
    n = head.next.next.next.next
    if head.v is not None:
        if n is not None:
            n.reset()
        y = head.v  # SUBJECT
        print("Y", y)


def main() -> None:
    a = L(None)
    b = L(a)
    a.next = b
    f(a)
''')

row("ptr_hop4_store", P, CK, "n = head.next.next.next.next; n.v store kills head.v", PRE_L, '''
def f(head: L, opt: int | None) -> None:
    n = head.next.next.next.next
    if head.v is not None:
        if n is not None:
            n.v = opt
        y = head.v  # SUBJECT
        print("Y", y)


def main() -> None:
    a = L(None)
    b = L(a)
    a.next = b
    f(a, None)
''')

row("ptr_hop6_call", P, CK, "six hops then n.reset() kills head.v", PRE_L, '''
def f(head: L) -> None:
    n = head.next.next.next.next.next.next
    if head.v is not None:
        if n is not None:
            n.reset()
        y = head.v  # SUBJECT
        print("Y", y)


def main() -> None:
    a = L(None)
    b = L(a)
    a.next = b
    f(a)
''')

row("ptr_second_param", P, CK, "store through a second L parameter reachable from the walked one kills node.v", PRE_L, '''
def f(head: L, other: L) -> None:
    node: Ptr[L] = head
    while node is not None:
        if node.v is not None:
            if node.n == 5:
                other.v = None
            y = node.v  # SUBJECT
            print("Y", y)
        node = node.next


def main() -> None:
    t2 = L(None)
    t2.n = 5
    h2 = L(t2)
    f(h2, t2)
''')

row("ptr_selfloop_call", P, CK, "a.next is a: a.next.reset() kills a.v", PRE_L, '''
def f(a: L) -> None:
    if a.v is not None:
        if a.next is not None:
            a.next.reset()
        y = a.v  # SUBJECT
        print("Y", y)


def main() -> None:
    x = L(None)
    x.next = x
    f(x)
''')

row("ptr_selfloop_store", P, CK, "a.next is a: nx = a.next; nx.v store kills a.v", PRE_L, '''
def f(a: L, opt: int | None) -> None:
    nx = a.next
    if a.v is not None:
        if nx is not None:
            nx.v = opt
        y = a.v  # SUBJECT
        print("Y", y)


def main() -> None:
    x = L(None)
    x.next = x
    f(x, None)
''')

row("ptr_selfloop_ptrfact", P, CK, "a.next is a: a.next.next = None kills the a.next non-null fact", PRE_L, '''
def f(a: L) -> None:
    if a.next is not None:
        a.next.next = None
        y = a.next.v  # SUBJECT
        print("Y", y)


def main() -> None:
    x = L(None)
    x.next = x
    f(x)
''', ["verdict = whether the a.next deref on the subject line is checked"], verdict="deref")

DLL = '''from tpy import Ptr, int32


class D:
    v: int | None
    prev: Ptr["D"]
    next: Ptr["D"]

    def __init__(self) -> None:
        self.v = 1
        self.prev = None
        self.next = None
'''

row("ptr_dll_prev", P, CK, "doubly linked: head.next.prev store kills head.v", DLL, '''
def f(head: D, opt: int | None) -> None:
    node = head.next
    if head.v is not None:
        if node is not None:
            p = node.prev
            if p is not None:
                p.v = opt
        y = head.v  # SUBJECT
        print("Y", y)


def main() -> None:
    a = D()
    b = D()
    a.next = b
    b.prev = a
    f(a, None)
''')

TREE = '''from tpy import Ptr, int32


class T:
    v: int | None
    n: int32
    l: Ptr["T"]
    r: Ptr["T"]

    def __init__(self) -> None:
        self.v = 1
        self.n = 0
        self.l = None
        self.r = None
'''

TREE_MAIN = '''
def main() -> None:
    root = T()
    c1 = T()
    c2 = T()
    root.l = c1
    root.r = c2
    c1.l = root
    f(root, None, 0)
'''

row("ptr_tree_descent", P, CK, "binary descent node = node.l if c else node.r; node.v store kills root.v", TREE, '''
def f(root: T, opt: int | None, bits: int) -> None:
    node: Ptr[T] = root
    k = bits
    i = 0
    while node is not None and i < 2:
        node = node.l if k % 2 == 0 else node.r
        k = k // 2
        i += 1
    if root.v is not None:
        if node is not None:
            node.v = opt
        y = root.v  # SUBJECT
        print("Y", y)
''' + TREE_MAIN, ["truth graph has a back edge c1.l = root (a tree type does not prove acyclicity)"])

row("ptr_tree_descent_sibling", P, UN, "binary descent; node.n store keeps root.v", TREE, '''
def f(root: T, opt: int | None, bits: int) -> None:
    node: Ptr[T] = root
    k = bits
    i = 0
    while node is not None and i < 2:
        node = node.l if k % 2 == 0 else node.r
        k = k // 2
        i += 1
    if root.v is not None:
        if node is not None:
            node.n += 1
        y = root.v  # SUBJECT
        print("Y", y, opt)
''' + TREE_MAIN)

row("ptr_tree_descent_root_kept", P, UN, "binary descent, store through node.v; fact on a T NOT reachable (a fresh local)", TREE, '''
def f(root: T, opt: int | None, bits: int) -> None:
    node: Ptr[T] = root
    k = bits
    i = 0
    while node is not None and i < 2:
        node = node.l if k % 2 == 0 else node.r
        k = k // 2
        i += 1
    s = T()
    if s.v is not None:
        if node is not None:
            node.v = opt
        y = s.v  # SUBJECT
        print("Y", y)
''' + TREE_MAIN, ["the rule as written ('may be ANY place whose type is T') kills it; a fresh local whose storage never escapes cannot be behind a pointer, so a refined model keeps it -- truth stays-value"])

row("ptr_parent", P, CK, "parent pointer: root.child.parent store kills root.v", '''from tpy import Ptr


class Q:
    v: int | None
    parent: Ptr["Q"]
    child: Ptr["Q"]

    def __init__(self) -> None:
        self.v = 1
        self.parent = None
        self.child = None
''', '''
def f(root: Q, opt: int | None) -> None:
    c = root.child
    if root.v is not None:
        if c is not None:
            up = c.parent
            if up is not None:
                up.v = opt
        y = root.v  # SUBJECT
        print("Y", y)


def main() -> None:
    r = Q()
    k = Q()
    r.child = k
    k.parent = r
    f(r, None)
''')

TWO = '''from tpy import Ptr


class B:
    v: int | None
    next: Ptr["B"]

    def __init__(self) -> None:
        self.v = 1
        self.next = None

    def reset(self) -> None:
        self.v = None


class A:
    v: int | None
    next: Ptr["A"]
    bp: Ptr[B]

    def __init__(self) -> None:
        self.v = 1
        self.next = None
        self.bp = None

    def reset(self) -> None:
        self.v = None

    def poke(self) -> None:
        if self.bp is not None:
            self.bp.reset()
'''

TWO_MAIN = '''
def main() -> None:
    a1 = A()
    a2 = A()
    a1.next = a2
    b1 = B()
    b2 = B()
    b1.next = b2
    a2.bp = b2
    f(a1, b1, None)
'''

TWO_UNLINKED = TWO.replace("    bp: Ptr[B]\n", "").replace("        self.bp = None\n", "").split("    def poke")[0]
TWO_MAIN_UNLINKED = TWO_MAIN.replace("    a2.bp = b2\n", "")

TWO_WALK = '''
def f(ha: A, hb: B, opt: int | None) -> None:
    na = ha.next
    nb = hb.next
    if nb is not None:
        if nb.v is not None:
            if na is not None:
                %s
            y = nb.v  # SUBJECT
            print("Y", y)
'''

row("ptr_two_types_store", P, UN, "store through pointer-reached A keeps a fact on pointer-reached B", TWO_UNLINKED,
    TWO_WALK % "na.v = opt" + TWO_MAIN_UNLINKED, ["type precision: A's v slot cannot be a B's"])
row("ptr_two_types_call", P, UN, "na.reset() on an A with no path to B keeps a fact on pointer-reached B", TWO_UNLINKED,
    TWO_WALK % "na.reset()" + TWO_MAIN_UNLINKED, ["type precision: B is not reachable from A's type"])
row("ptr_two_types_linked_store", P, UN, "plain na.v store on an A that links to B keeps a fact on a B", TWO,
    TWO_WALK % "na.v = opt" + TWO_MAIN, ["a plain store writes one slot (A-object, v); no B can be that object"])
row("ptr_two_types_linked_call", P, CK, "na.poke() writes through A.bp: kills a fact on a B", TWO,
    TWO_WALK % "na.poke()" + TWO_MAIN, ["B is reachable from A through the bp indirection field"])

row("ptr_inline_vs_ptr", P, CK, "store through o.nxt (some M2) kills a fact on the inline place a.inner.v", '''from tpy import Ptr


class M2:
    v: int | None
    w: int | None
    nxt: Ptr["M2"]

    def __init__(self) -> None:
        self.v = 1
        self.w = 1
        self.nxt = None


class H2:
    inner: M2

    def __init__(self) -> None:
        self.inner = M2()
''', '''
def f(a: H2, o: M2, opt: int | None) -> None:
    m = o.nxt
    if a.inner.v is not None:
        if m is not None:
            m.v = opt
        y = a.inner.v  # SUBJECT
        print("Y", y)


def main() -> None:
    a = H2()
    o = M2()
    o.nxt = a.inner
    f(a, o, None)
''')

row("ptr_inline_vs_ptr_otherfield", P, UN, "store of m.w through o.nxt keeps a.inner.v", '''from tpy import Ptr


class M2:
    v: int | None
    w: int | None
    nxt: Ptr["M2"]

    def __init__(self) -> None:
        self.v = 1
        self.w = 1
        self.nxt = None


class H2:
    inner: M2

    def __init__(self) -> None:
        self.inner = M2()
''', '''
def f(a: H2, o: M2, opt: int | None) -> None:
    m = o.nxt
    if a.inner.v is not None:
        if m is not None:
            m.w = opt
        y = a.inner.v  # SUBJECT
        print("Y", y)


def main() -> None:
    a = H2()
    o = M2()
    o.nxt = a.inner
    f(a, o, None)
''')

row("ptr_chain_in_walk", P, CK, "inside a walk: w = node.m; u = w; p = node; p.m.reset() kills u.v", '''from tpy import Ptr


class M:
    v: int | None

    def __init__(self) -> None:
        self.v = 1

    def reset(self) -> None:
        self.v = None


class L:
    v: int | None
    m: M
    next: Ptr["L"]

    def __init__(self, nxt: Ptr["L"]) -> None:
        self.v = 1
        self.m = M()
        self.next = nxt
''', '''
def f(head: L) -> None:
    node: Ptr[L] = head
    while node is not None:
        w = node.m
        u = w
        p = node
        if u.v is not None:
            p.m.reset()
            y = u.v  # SUBJECT
            print("Y", y)
        node = node.next


def main() -> None:
    tail = L(None)
    head = L(tail)
    f(head)
''')

row("ptr_box", P, CK, "Box[BL]-owned chain: head.next.get().reset() vs fact head.v", '''from __future__ import annotations
from tplib import Box


class BL:
    v: int | None
    next: Box[BL] | None

    def __init__(self) -> None:
        self.v = 1
        self.next = None

    def reset(self) -> None:
        self.v = None
''', '''
def f(head: BL) -> None:
    nb = head.next
    if head.v is not None:
        if nb is not None:
            nb.get().reset()
        y = head.v  # SUBJECT
        print("Y", y)


def main() -> None:
    h = BL()
    h.next = Box(BL())
    f(h)
''', ["model: Box is an indirection -> abstract BL may be head -> kill; truth stays-value (unique ownership cannot loop back)"])

row("ptr_rc", P, CK, "Rc[RL] ring: nx.get().reset() through head.next kills head.v", '''from __future__ import annotations
from tplib import Rc


class RL:
    v: int | None
    next: Rc[RL] | None

    def __init__(self) -> None:
        self.v = 1
        self.next = None

    def reset(self) -> None:
        self.v = None
''', '''
def f(head: RL) -> None:
    n1 = head.next
    if head.v is not None:
        if n1 is not None:
            n2 = n1.get().next
            if n2 is not None:
                n2.get().reset()
        y = head.v  # SUBJECT
        print("Y", y)


def main() -> None:
    a = Rc.new(RL())
    b = Rc.new(RL())
    a.get().next = b.clone()
    b.get().next = a.clone()
    f(a.get())
''')

row("ptr_recursive_optional", P, CK, "recursive `RO | None` field walk; node.reset() kills head.v", '''from __future__ import annotations


class RO:
    v: int | None
    next: RO | None

    def __init__(self) -> None:
        self.v = 1
        self.next = None

    def reset(self) -> None:
        self.v = None
''', '''
def f(head: RO) -> None:
    node = head.next
    if head.v is not None:
        if node is not None:
            nn = node.next
            if nn is not None:
                nn.reset()
        y = head.v  # SUBJECT
        print("Y", y)


def main() -> None:
    a = RO()
    b = RO()
    a.next = b
    b.next = a
    f(a)
''')

row("ptr_recursive_optional_walk", P, UN, "recursive `RO | None` walk node = node.next; sibling store keeps node.v", '''from __future__ import annotations
from tpy import int32


class RO:
    v: int | None
    n: int32
    next: RO | None

    def __init__(self) -> None:
        self.v = 1
        self.n = 0
        self.next = None
''', '''
def f(head: RO) -> None:
    node = head.next
    while node is not None:
        if node.v is not None:
            node.n += 1
            y = node.v  # SUBJECT
            print("Y", y)
        node = node.next


def main() -> None:
    a = RO()
    b = RO()
    c = RO()
    a.next = b
    b.next = c
    f(a)
''', ["walk starts at head.next: `node: RO | None = head` rejects (decl.slot_type) and a `while True` reseat from head rejects (reference may outlive its storage)"])

row("ptr_recursive_optional_walk_kill", P, CK, "recursive `RO | None` walk calling node.reset() kills a fact on head.next.next", '''from __future__ import annotations
from tpy import int32


class RO:
    v: int | None
    n: int32
    next: RO | None

    def __init__(self) -> None:
        self.v = 1
        self.n = 0
        self.next = None

    def reset(self) -> None:
        self.v = None
''', '''
def f(head: RO) -> None:
    h1 = head.next
    if h1 is not None:
        h2 = h1.next
        if h2 is not None:
            if h2.v is not None:
                node = head.next
                while node is not None:
                    node.reset()
                    node = node.next
                y = h2.v  # SUBJECT
                print("Y", y)


def main() -> None:
    a = RO()
    b = RO()
    c = RO()
    a.next = b
    b.next = c
    f(a)
''')

# ---------------------------------------------------------------- unmodelled
row("unm_two_params", U, CK, "f(n, n): store through b kills a.v at run time", PRE_NM, '''
def f(a: N, b: N) -> None:
    if a.v is not None:
        b.v = None
        y = a.v  # SUBJECT
        print("Y", y)


def main() -> None:
    n = N()
    f(n, n)
''')

row("unm_subscript_local", U, CK, "e = xs[0]; clear_m(xs[0]) kills e.v at run time", PRE_NM, '''
def f(xs: list[M]) -> None:
    e = xs[0]
    if e.v is not None:
        clear_m(xs[0])
        y = e.v  # SUBJECT
        print("Y", y)


def main() -> None:
    f([M()])
''')

row("unm_property_setter", U, CK, "o.p = 1 runs a setter that writes the sibling o.q", '''
class O:
    q: int | None
    _p: int

    def __init__(self) -> None:
        self.q = 1
        self._p = 0

    @property
    def p(self) -> int:
        return self._p

    @p.setter
    def p(self, v: int) -> None:
        self._p = v
        self.q = None
''', '''
def f(o: O) -> None:
    if o.q is not None:
        o.p = 1
        y = o.q  # SUBJECT
        print("Y", y)


def main() -> None:
    f(O())
''')

row("unm_nested_def_before", U, CK, "closure defined before the narrowing writes the captured record's field", PRE_NM, '''
def f() -> None:
    t = N()

    def wipe() -> None:
        t.v = None

    if t.v is not None:
        wipe()
        y = t.v  # SUBJECT
        print("Y", y)


def main() -> None:
    f()
''')

row("unm_nested_alias_outer", U, CK, "store inside a nested def through an alias bound in the outer body", PRE_NM, '''
def f(a: N, c: bool) -> None:
    t = a if c else N()
    if a.v is not None:

        def k() -> None:
            t.v = None
        k()
        y = a.v  # SUBJECT
        print("Y", y)


def main() -> None:
    f(N(), True)
''', ["alias bound BEFORE the narrowing so the plain-bind kill (which fires when t is bound inside the narrowed block) does not mask the closure store"])

row("unm_nested_alias_inner", U, CK, "narrowing and store both inside a nested def, the store through a captured alias bound in the outer body", PRE_NM, '''
def f(a: N, c: bool) -> None:
    t = a if c else N()

    def k() -> None:
        if a.v is not None:
            t.v = None
            y = a.v  # SUBJECT
            print("Y", y)
    k()


def main() -> None:
    f(N(), True)
''', ["a nested def's may-hold relation is built from its own body, so it never learns that t may hold a"])

row("unm_match_kw_capture", U, CK, "match a: case N(inner=u): clear_m(a.inner) kills u.v at run time", PRE_NM, '''
def f(a: N) -> None:
    match a:
        case N(inner=u):
            if u.v is not None:
                clear_m(a.inner)
                y = u.v  # SUBJECT
                print("Y", y)


def main() -> None:
    f(N())
''')

IADD = '''
class Bag:
    v: int | None

    def __init__(self) -> None:
        self.v = 1

    def __iadd__(self, k: int) -> "Bag":
        self.v = None
        return self


class H:
    bag: Bag

    def __init__(self) -> None:
        self.bag = Bag()
'''

row("unm_iadd_local", U, CK, "b += 1 resolves to Bag.__iadd__ which stores b.v = None", IADD, '''
def f(b: Bag) -> None:
    if b.v is not None:
        b += 1
        y = b.v  # SUBJECT
        print("Y", y)


def main() -> None:
    f(Bag())
''')

row("unm_iadd_alias", U, CK, "t.bag += 1 through an alias of h (user __iadd__), fact h.bag.v", IADD, '''
def f(h: H, c: bool) -> None:
    t = h if c else H()
    if h.bag.v is not None:
        t.bag += 1
        y = h.bag.v  # SUBJECT
        print("Y", y)


def main() -> None:
    f(H(), True)
''', ["filed as unmodelled (user __iadd__), but `t.bag += 1` also stores the slot (t, bag) which the fact h.bag.v sits under -- the plain store rule alone would kill it"])


# ---------------------------------------------------------------- everyday
# Shapes with no pointer-like field: the verdict is the one the tree at
# cb82cc41ee gives (the fact survives), and CPython agrees.
E, V = "everyday", "view"

PRE_EV = '''class Point:
    x: int
    py: int | None

    def __init__(self, x: int, py: int | None) -> None:
        self.x = x
        self.py = py

    def shift(self, d: int) -> None:
        self.x += d


class Rect:
    a: Point
    b: Point

    def __init__(self) -> None:
        self.a = Point(0, 1)
        self.b = Point(2, 3)

    @property
    def corner(self) -> Point:
        return self.b


class Item:
    pos: Point
    tags: list[str]

    def __init__(self) -> None:
        self.pos = Point(0, 1)
        self.tags = []


class Cfg:
    limit: int | None

    def __init__(self) -> None:
        self.limit = 10
'''

row("ev_loop_alias_store", E, UN, "loop body: q = r.b; q.py = 7 keeps p.y (ev4 v1)", PRE_EV, '''
def f(p: Point, rs: list[Rect]) -> None:
    if p.py is not None:
        for r in rs:
            q = r.b
            q.py = 7
            y = p.py  # SUBJECT
            print("Y", y)


def main() -> None:
    f(Point(0, 1), [Rect()])
''')

row("ev_loop_alias_call", E, UN, "loop body: q = r.b; q.shift(1) keeps p.y (ev4 v2)", PRE_EV, '''
def f(p: Point, rs: list[Rect]) -> None:
    if p.py is not None:
        for r in rs:
            q = r.b
            q.shift(1)
            y = p.py  # SUBJECT
            print("Y", y)


def main() -> None:
    f(Point(0, 1), [Rect()])
''')

row("ev_loopvar_field_call", E, UN, "for it in items: it.pos.shift(1) keeps cfg.limit (ev5 w1)", PRE_EV, '''
def f(cfg: Cfg, items: list[Item]) -> None:
    if cfg.limit is not None:
        for it in items:
            it.pos.shift(1)
            y = cfg.limit  # SUBJECT
            print("Y", y)


def main() -> None:
    f(Cfg(), [Item()])
''')

row("ev_loopvar_list_append", E, UN, "for it in items: it.tags.append keeps cfg.limit (ev5 w2)", PRE_EV, '''
def f(cfg: Cfg, items: list[Item]) -> None:
    if cfg.limit is not None:
        for it in items:
            it.tags.append("x")
            y = cfg.limit  # SUBJECT
            print("Y", y)


def main() -> None:
    f(Cfg(), [Item()])
''')

row("ev_loopvar_field_store", E, UN, "for it in items: it.pos.x = 3 keeps cfg.limit (ev5 w3)", PRE_EV, '''
def f(cfg: Cfg, items: list[Item]) -> None:
    if cfg.limit is not None:
        for it in items:
            it.pos.x = 3
            y = cfg.limit  # SUBJECT
            print("Y", y)


def main() -> None:
    f(Cfg(), [Item()])
''')

row("ev_subscript_local_call", E, UN, "it = items[i]; it.pos.shift(1) keeps cfg.limit (ev6 x1)", PRE_EV, '''
def f(cfg: Cfg, items: list[Item]) -> None:
    if cfg.limit is not None:
        for i in range(len(items)):
            it = items[i]
            it.pos.shift(1)
            y = cfg.limit  # SUBJECT
            print("Y", y)


def main() -> None:
    f(Cfg(), [Item()])
''')

row("ev_dict_items_append", E, UN, "for k, v in d.items(): v.tags.append(k) keeps cfg.limit (ev6 x2)", PRE_EV, '''
def f(cfg: Cfg, d: dict[str, Item]) -> None:
    if cfg.limit is not None:
        for k, v in d.items():
            v.tags.append(k)
            y = cfg.limit  # SUBJECT
            print("Y", y)


def main() -> None:
    f(Cfg(), {"a": Item()})
''')

row("ev_loop_alias_after", E, UN, "p = it.pos; p.shift(1) in a loop, read after it (ev6 x4)", PRE_EV, '''
def f(cfg: Cfg, items: list[Item]) -> None:
    if cfg.limit is not None:
        for it in items:
            p = it.pos
            p.shift(1)
        y = cfg.limit  # SUBJECT
        print("Y", y)


def main() -> None:
    f(Cfg(), [Item()])
''')

row("ev_property_store", E, UN, "k = other.corner (a property); k.py = None keeps r.a.y (ev3 u5)", PRE_EV, '''
def f(r: Rect, other: Rect) -> None:
    if r.a.py is not None:
        k = other.corner
        k.py = None
        y = r.a.py  # SUBJECT
        print("Y", y)


def main() -> None:
    f(Rect(), Rect())
''')

row("ev_loop_first_store", E, UN, "first = rs[0]; loop q = r.b; q.py = 7 keeps first.a.y (ev3 u6)", PRE_EV, '''
def f(rs: list[Rect]) -> None:
    first = rs[0]
    if first.a.py is not None:
        for r in rs:
            q = r.b
            q.py = 7
            y = first.a.py  # SUBJECT
            print("Y", y)


def main() -> None:
    f([Rect(), Rect()])
''')

# Value copies, non-None stores through an alias, walk stores: the
# branch point's verdict (the fact survives), and CPython agrees.
PRE_COPY = '''from tpy import Ptr, int32


class P:
    x: int32

    def __init__(self) -> None:
        self.x = 5


class K:
    v: int | None
    s: str | None
    p: Ptr[P]

    def __init__(self, p: Ptr[P]) -> None:
        self.v = 1
        self.s = "hi"
        self.p = p
'''

row("ev_copy_int", E, UN, "x = a.v (int | None) is a copy: a.v = None leaves x", PRE_COPY, '''
def f(a: K) -> None:
    x = a.v
    if x is not None:
        a.v = None
        y = x + 1  # SUBJECT
        print("Y", y)


def main() -> None:
    f(K(None))
''')

row("ev_copy_str", E, UN, "s = a.s (str | None) is a copy: a.s = None leaves s", PRE_COPY, '''
def f(a: K) -> None:
    s = a.s
    if s is not None:
        a.s = None
        y = s  # SUBJECT
        print("Y", y.upper())


def main() -> None:
    f(K(None))
''')

row("ev_copy_ptr", E, UN, "p = h.p (a Ptr) is a copy: h.p = None leaves p", PRE_COPY, '''
def f(h: K) -> None:
    p = h.p
    if p is not None:
        h.p = None
        y = p.x  # SUBJECT
        print("Y", y)


def main() -> None:
    q = P()
    f(K(q))
''', verdict="deref")

row("ev_store_nonnone_ternary", E, UN, "t = a if c else b; t.v = 5 keeps a.v (e24 setv)", PRE_NM, '''
def f(a: N, b: N, c: bool) -> None:
    t = a if c else b
    if a.v is not None:
        t.v = 5
        y = a.v  # SUBJECT
        print("Y", y)


def main() -> None:
    f(N(), N(), True)
''')

row("ev_store_nonnone_copy", E, UN, "t = h; t.v = 7 keeps h.v (e24 setv_chain)", PRE_NM, '''
def f(h: N) -> None:
    t = h
    if h.v is not None:
        t.v = 7
        y = h.v  # SUBJECT
        print("Y", y)


def main() -> None:
    f(N())
''')

row("ev_store_nonnone_handler", E, UN, "t = a if c else N(); t.v = 2 in a try body keeps a.v in the handler (hf3)", PRE_NM, '''
def f(a: N, c: bool) -> None:
    t = a if c else N()
    if a.v is not None:
        try:
            t.v = 2
            raise ValueError("stop")
        except ValueError:
            y = a.v  # SUBJECT
            print("Y", y)


def main() -> None:
    f(N(), True)
''')

row("ev_walk_store_nonnone", E, UN, "a Ptr walk from r, then n.v = 5 through the walk keeps r.v (many)", '''from tpy import Ptr


class R:
    v: int | None
    nx: Ptr["R"]

    def __init__(self, nx: Ptr["R"]) -> None:
        self.v = 1
        self.nx = nx
''', '''
def f(r: R, k: int) -> None:
    n: Ptr[R] = r
    i = 0
    while i < k:
        if n is not None:
            n = n.nx
        i += 1
    if r.v is not None:
        if n is not None:
            n.v = 5
        y = r.v  # SUBJECT
        print("Y", y)


def main() -> None:
    t = R(None)
    f(R(t), 1)
''')


# ---------------------------------------------------------------- name reuse
PRE_REUSE = '''from tpy import Ptr


class M:
    v: int | None

    def __init__(self) -> None:
        self.v = 1


class A:
    inner: M

    def __init__(self) -> None:
        self.inner = M()


class B:
    inner: Ptr[M]

    def __init__(self, m: Ptr[M]) -> None:
        self.inner = m
'''

row("reuse_cross_loops", P, CK, "u bound through a B (pointer hop) in loop 1, stored through in loop 2 where n is an A", PRE_REUSE, '''
def f(xs: list[A], ys: list[B], m: M, opt: int | None) -> None:
    u: Ptr[M] = None
    for n in ys:
        u = n.inner
    for n in xs:
        if m.v is not None:
            if u is not None:
                u.v = opt
            y = m.v  # SUBJECT
            print("Y", y)


def main() -> None:
    m = M()
    f([A()], [B(m)], m, None)
''')

row("reuse_loopvar_types", P, CK, "loop variable n is an A in loop 1 and a B in loop 2; the store through the B binding kills m.v", PRE_REUSE, '''
def f(xs: list[A], ys: list[B], m: M, opt: int | None) -> None:
    for n in xs:
        u = n.inner
        if m.v is not None:
            u.v = 3
            print("first:", m.v)
    for n in ys:
        u2 = n.inner
        if m.v is not None:
            if u2 is not None:
                u2.v = opt
            y = m.v  # SUBJECT
            print("Y", y)


def main() -> None:
    m = M()
    f([A()], [B(m)], m, None)
''')

# ---------------------------------------------------------------- view
PRE_VIEW = '''from tpy import String


class M:
    v: int

    def __init__(self) -> None:
        self.v = 1


class R:
    inner: M

    def __init__(self) -> None:
        self.inner = M()
'''

row("view_record_after", V, "VIEW", "x = st.strip() stays a view when a record local is bound after it (f_plain)", PRE_VIEW, '''
def f() -> None:
    st = String("  hello  ")
    y = st.strip()  # SUBJECT
    m = M()
    print(m.v)
    print("Y", y)


def main() -> None:
    f()
''', verdict="view")

row("view_record_before", V, "VIEW", "the same with the record local bound before the view (f_plain_before)", PRE_VIEW, '''
def f() -> None:
    m = M()
    st = String("  hello  ")
    y = st.strip()  # SUBJECT
    print(m.v)
    print("Y", y)


def main() -> None:
    f()
''', verdict="view")

row("view_loop_projection", V, "VIEW", "a loop binds u = item.inner after the view (f_late)", PRE_VIEW, '''
def f(items: list[R]) -> None:
    st = String("  hello  ")
    y = st.strip()  # SUBJECT
    for item in items:
        u = item.inner
        print(u.v)
    print("Y", y)


def main() -> None:
    f([R()])
''', verdict="view")

row("view_pointer_walk", V, "VIEW", "a linked-list walk beside the view of an unrelated String (f_view)", '''from tpy import String


class L:
    v: int | None
    next: "L | None"

    def __init__(self, nx: "L | None") -> None:
        self.v = 1
        self.next = nx
''', '''
def f(head: L) -> None:
    st = String("  hello  ")
    y = st.strip()  # SUBJECT
    node = head.next
    while node is not None:
        print(node.v)
        node = node.next
    print("Y", y)


def main() -> None:
    f(L(L(None)))
''', verdict="view")

# ---------------------------------------------------------------- meets
# The loop / handler / finally entry kill-set (prescan.FactKills) and the
# closure kills: the shapes of TODO.md "One write collector in the pre-scan"
# step 2b, one must-kill row and its must-stay-silent twin each.
row("meet_match_capture_loop", K, CK, "a match capture in the loop body rebinds the narrowed local on the next pass", "", '''
def f(v: int | None) -> None:
    x: int | None = 5
    if x is not None:
        i = 0
        while i < 2:
            y = x  # SUBJECT
            print("Y", y)
            match v:
                case x:
                    pass
            i += 1


def main() -> None:
    f(None)
''')

row("meet_match_capture_loop_other", K, UN, "a match capture of ANOTHER name keeps the narrowing across the loop", "", '''
def f(v: int | None) -> None:
    x: int | None = 5
    if x is not None:
        i = 0
        while i < 2:
            y = x  # SUBJECT
            print("Y", y)
            match v:
                case z:
                    pass
            i += 1


def main() -> None:
    f(None)
''')

row("meet_closure_nonlocal_before_loop", K, CK, "a closure defined before the loop rebinds the narrowed local through nonlocal when the body calls it", "", '''
def f() -> None:
    x: int | None = 5

    def clear() -> None:
        nonlocal x
        x = None

    if x is not None:
        i = 0
        while i < 2:
            y = x  # SUBJECT
            print("Y", y)
            clear()
            i += 1


def main() -> None:
    f()
''')

row("meet_closure_nonlocal_before_loop_uncalled", K, UN, "a closure defined before the loop that the body never calls keeps the narrowing", "", '''
def f() -> None:
    x: int | None = 5

    def clear() -> None:
        nonlocal x
        x = None

    if x is not None:
        i = 0
        while i < 2:
            y = x  # SUBJECT
            print("Y", y)
            i += 1
        clear()


def main() -> None:
    f()
''')

row("meet_closure_nonlocal_before_handler", K, CK, "a closure rebinding the narrowed local through nonlocal runs in the try body; the handler reads it", "", '''
def boom() -> None:
    raise ValueError("boom")


def f() -> None:
    x: int | None = 5

    def clear() -> None:
        nonlocal x
        x = None

    if x is not None:
        try:
            clear()
            boom()
        except ValueError:
            y = x  # SUBJECT
            print("Y", y)


def main() -> None:
    f()
''')

row("meet_closure_field_write_loop", K, CK, "a closure defined before the loop stores None through a captured record when the body calls it", PRE_NM, '''
def f() -> None:
    t = N()

    def wipe() -> None:
        t.v = None

    if t.v is not None:
        i = 0
        while i < 2:
            y = t.v  # SUBJECT
            print("Y", y)
            wipe()
            i += 1


def main() -> None:
    f()
''')

row("meet_closure_field_write_loop_other", K, UN, "a closure storing through ANOTHER captured record keeps the narrowing across the loop", PRE_NM, '''
def f() -> None:
    t = N()
    u = N()

    def wipe() -> None:
        u.v = None

    if t.v is not None:
        i = 0
        while i < 2:
            y = t.v  # SUBJECT
            print("Y", y)
            wipe()
            i += 1


def main() -> None:
    f()
''')

row("meet_two_params_distinct", K, UN, "two same-typed parameters passed distinct objects: a store through b keeps a.v (the cost of a parameter edge)", PRE_NM, '''
def f(a: N, b: N) -> None:
    if a.v is not None:
        b.v = None
        y = a.v  # SUBJECT
        print("Y", y)


def main() -> None:
    f(N(), N())
''')

row("meet_two_params_distinct_str", K, "COMPILES", "two same-typed parameters, a str | None field: a kill here is a compile error on valid code (a sema parameter edge was dropped for this reason, BUGS.md#may-hold-relation-unmodelled-shapes (1))", "", '''
class S:
    name: str | None

    def __init__(self) -> None:
        self.name = "a"


def f(a: S, b: S) -> None:
    if a.name is not None:
        b.name = None
        y = a.name.upper()  # SUBJECT
        print("Y", y)


def main() -> None:
    f(S(), S())
''', verdict="compiles")

PRE_O = """from tpy import readonly


class O:
    def __init__(self, name: str | None) -> None:
        self.name = name

    @readonly
    def describe(self) -> str:
        return "O"

    def describe_inferred(self) -> str:
        return "O"
"""

row("meet_closure_readonly_receiver_compiles", K, "COMPILES", "a closure calling a @readonly method on the capture runs in the narrowed block: a str | None field stays narrowed", PRE_O, '''
def f() -> None:
    o = O("a")

    def peek() -> None:
        print("peek", o.describe())

    if o.name is not None:
        peek()
        y = o.name.upper()  # SUBJECT
        print("Y", y)


def main() -> None:
    f()
''', ["a closure's method receivers are exported syntactically: @readonly stops writes through self only, so the callee may still write what the receiver's pointer fields reach, and a direct call kills beneath its receiver the same way"],
    verdict="compiles")

row("meet_closure_readonly_inferred", K, "COMPILES", "a closure calling an UNDECORATED read-only method on the capture runs in the narrowed block", PRE_O, '''
def f() -> None:
    o = O("a")

    def peek() -> None:
        print("peek", o.describe_inferred())

    if o.name is not None:
        peek()
        y = o.name.upper()  # SUBJECT
        print("Y", y)


def main() -> None:
    f()
''', ["the same rule as meet_closure_readonly_receiver_compiles: the receiver is exported whatever the callee",
      "before this branch the shape compiled: the previous rule did not export a closure's writes through captured storage at all (its field-store face, unm_nested_def_before, was a silent miscompile)"],
    verdict="compiles")

row("meet_closure_store_nonnone_compiles", K, "COMPILES", "a closure whose only store keeps the captured str | None field non-None runs in the narrowed block", PRE_O, '''
def f() -> None:
    o = O("a")

    def rename() -> None:
        o.name = "b"

    if o.name is not None:
        rename()
        y = o.name.upper()  # SUBJECT
        print("Y", y)


def main() -> None:
    f()
''', ["the def site decides each exported path's store verdict (stores_keep) over the analyzed closure body, as a meet does for a field store in its block; a store of None still kills (case closure_and_capture_kills, field_straight)"],
    verdict="compiles")

row("meet_closure_before_loop_record", K, "COMPILES", "a closure rebinding a narrowed record Optional through nonlocal is defined before a loop whose body calls only builtins and a plain function", "", '''
class N:
    def __init__(self, v: str) -> None:
        self.v = v


def use(n: N) -> str:
    return n.v


def f() -> None:
    x: N | None = N("a")

    def wipe() -> None:
        nonlocal x
        x = None

    if x is not None:
        i = 0
        while i < 2:
            y = use(x)  # SUBJECT
            print("Y", y)
            i += 1
    wipe()


def main() -> None:
    f()
''', ["print() and use() cannot run wipe, but every call counts as one that may: the loop-entry meet kills x and use(x) is a type error",
      "before this branch the straight-line twin already rejected (the live path kills after print()); the back edge restored the facts, which was a silent miscompile when a call did run the closure (probe /tmp/agents/meta-wc/c3e.py)"],
    verdict="compiles")

# ---------------------------------------------------------------- residuals
PRE_PL = '''from tpy import Ptr


class L:
    v: int | None
    next: Ptr["L"]

    def __init__(self) -> None:
        self.v = 1
        self.next = None

    def reset(self) -> None:
        self.v = None

    def reset_all(self) -> None:
        self.v = None
        n = self.next
        while n is not None:
            n.reset()
            n = n.next
'''

row("res_recv_walk", U, CK, "a.reset_all() walks a's pointer fields and reaches a local b linked in (BUGS.md#mutating-call-walk-keeps-linked-local-fact)", PRE_PL, '''
def f() -> None:
    a = L()
    b = L()
    a.next = b
    if b.v is not None:
        a.reset_all()
        y = b.v  # SUBJECT
        print("Y", y)


def main() -> None:
    f()
''')

row("res_ptr_param_root", U, CK, "a Ptr[L] parameter aliasing an L parameter (BUGS.md#may-hold-relation-unmodelled-shapes)", PRE_PL, '''
def f(p: Ptr[L], a: L, opt: int | None) -> None:
    if a.v is not None:
        if p is not None:
            p.v = opt
        y = a.v  # SUBJECT
        print("Y", y)


def main() -> None:
    a = L()
    f(a, a, None)
''')

row("res_ptr_call_root", U, CK, "p = take_ptr(a): a pointer from a call (BUGS.md#may-hold-relation-unmodelled-shapes)", PRE_PL, '''
def take_ptr(a: L) -> Ptr[L]:
    return a


def f(a: L, opt: int | None) -> None:
    p = take_ptr(a)
    if a.v is not None:
        if p is not None:
            p.v = opt
        y = a.v  # SUBJECT
        print("Y", y)


def main() -> None:
    a = L()
    f(a, None)
''')

row("res_back_edge", U, CK, "q = r.next bound inside the loop: its kill is needed at loop entry, before sema analyzed it (BUGS.md#may-hold-relation-unmodelled-shapes)", PRE_PL, '''
def f(head: L, rs: list[L]) -> None:
    if head.v is not None:
        for r in rs:
            y = head.v  # SUBJECT
            print("Y", y)
            q = r.next
            if q is not None:
                q.reset()


def main() -> None:
    head = L()
    rs = [L(), L()]
    rs[0].next = head
    f(head, rs)
''')

row("res_pending_deref", U, CK, "a deref queued in the statement whose call cuts it (BUGS.md#subexpression-right-to-left-eval)", '''from tpy import Ptr


class L:
    v: int | None
    n: int
    next: Ptr["L"]

    def __init__(self) -> None:
        self.v = 1
        self.n = 7
        self.next = None

    def cut(self) -> int:
        self.next = None
        return 0


def use(x: int, z: int) -> None:
    print("use:", x, z)
''', '''
def f(a: L) -> None:
    use(a.cut(), a.next.n)
    y = a.next.v  # SUBJECT
    print("Y", y)


def main() -> None:
    a = L()
    b = L()
    a.next = b
    f(a)
''', verdict="deref")

row("res_two_loopvars", U, CK, "two loop variables over the same list (BUGS.md#subscript-element-not-keyed)", '''class Leaf:
    v: int | None

    def __init__(self, v: int | None) -> None:
        self.v = v

    def reset(self) -> None:
        self.v = None


class A:
    f: Leaf

    def __init__(self) -> None:
        self.f = Leaf(1)
''', '''
def f(xs: list[A]) -> None:
    for n in xs:
        for m in xs:
            if n.f.v is not None:
                m.f.reset()
                y = n.f.v  # SUBJECT
                print("Y", y)


def main() -> None:
    f([A()])
''')

row("res_bare_borrow", U, CK, "o = a.opt borrows the payload; q.clear() through a pointer destroys it (BUGS.md#field-loan-whole-record-callee-unchecked)", '''from tpy import Ptr


class P:
    x: int

    def __init__(self, x: int) -> None:
        self.x = x


class L:
    opt: P | None
    next: Ptr["L"]

    def __init__(self) -> None:
        self.opt = P(5)
        self.next = None

    def clear(self) -> None:
        self.opt = None
''', '''
def f(a: L) -> None:
    q = a.next
    o = a.opt
    if o is not None:
        if q is not None:
            q.clear()
        y = o.x  # SUBJECT
        print("Y", y)


def main() -> None:
    s = L()
    s.next = s
    f(s)
''', verdict="deref")


# Spelling twins: the everyday spelling of a row whose matrix version had to
# be rewritten to lower; kept so the day it lowers it shows up.
SPELLINGS = [
    ("ptr_walk_samefield_other_lit", "ptr_walk_samefield_other", [("                q.v = opt", "                q.v = None")]),
    ("ptr_hop4_store_lit", "ptr_hop4_store", [("            n.v = opt", "            n.v = None")]),
    ("inl_composed_fwd_lit", "inl_composed_fwd", [("        a.inner.v = opt", "        a.inner.v = None")]),
    ("inl_deep_kill_onechain", "inl_deep_kill", None),
    ("inl_replace_ancestor_fullpath", "inl_replace_ancestor", [
        ("    hn = h.n\n    hi = hn.inner\n    if hi.v is not None:", "    if h.n.inner.v is not None:"),
        ("        y = hi.v  # SUBJECT", "        y = h.n.inner.v  # SUBJECT")]),
    ("ptr_recursive_optional_walk_decl", "ptr_recursive_optional_walk", [
        ("    node = head.next\n", "    node: RO | None = head\n")]),
    ("ptr_two_types_loopwalk", "ptr_two_types_store", [
        ("    na = ha.next\n    nb = hb.next\n    if nb is not None:\n        if nb.v is not None:",
         "    na: Ptr[A] = ha\n    while na is not None and na.next is not None:\n        na = na.next\n"
         "    nb: Ptr[B] = hb\n    while nb is not None and nb.next is not None:\n        nb = nb.next\n"
         "    if nb is not None:\n        if nb.v is not None:")]),
]

DEEP_ONECHAIN = '''
def f(a: K0) -> None:
    t = a.f.f.f.f.f
    if a.f.f.f.f.f.v is not None:
        t.v = None
        y = a.f.f.f.f.f.v  # SUBJECT
        print("Y", y)


def main() -> None:
    f(K0())
'''


_SPELLED = False


def spelling_rows() -> None:
    # Idempotent: a run and its report both generate.
    global _SPELLED
    if _SPELLED:
        return
    _SPELLED = True
    by_name = {r[0]: r for r in ROWS}
    for new, base, subs in SPELLINGS:
        name, group, expected, notes, verdict, desc, src = by_name[base]
        if subs is None:
            src = PRE_K + "\n" + DEEP_ONECHAIN
        else:
            for old, rep in subs:
                assert old in src, (new, old)
                src = src.replace(old, rep)
        ROWS.append((new, group, expected, [f"everyday spelling of {base}; rejects today -- verdict is REJECT until it lowers"],
                     verdict, desc + " (everyday spelling)", src))


# Rows whose verdict under the shipped rule (aliases of inline storage,
# exact places) differs from EXPECTED, with the reason: a filed defect by
# its BUGS.md slug, or a design decision. The gate compares against these.
_CLOSURE_RECEIVER_REASON = ("a closure's method receivers are exported syntactically: a @readonly callee may still write storage the receiver's pointer fields reach (probe shape: a readonly method storing None into a global a Ptr field points at), and a direct call on master kills beneath its receiver the same way; the precise answer is a Phase-2 callee effect (TODO.md \"Pre-scan write views: the leftovers\" (k)); the user's decision")
ACCEPTED: dict[str, tuple[str, str]] = {
    "inl_plain_bind_nostore": (CK, "the live path kills at a bare bind under the guard (pinned by none_safety/alias_store_kills_narrowing `plain_bind`; decided in TODO.md \"One fact-kill helper\")"),
    "ptr_walk_head_sibling": (CK, "the rebind node = node.next kills head.v: node holds head exactly and a rebind kills at the bind (the same decision as inl_plain_bind_nostore)"),
    "unm_iadd_alias": (UN, "BUGS.md#iadd-target-keeps-facts-beneath"),
    "meet_closure_nonlocal_before_loop_uncalled": (CK, "any call may run a closure that rebinds through nonlocal, and print() is a call: the loop-entry meet takes the live path's stance, and the live path already kills after print() on master; a body with no call keeps the fact (case closure_and_capture_kills, closure_loop_uncalled)"),
    "meet_closure_before_loop_record": ("REJECT", "the live path's rule: any call may run a closure that rebinds through nonlocal; before this branch the back edge restored the facts (silent miscompile /tmp/agents/meta-wc/c3e.py); the builtin-callee exemption is TODO.md \"Pre-scan write views: the leftovers\" (k)"),
    "meet_closure_readonly_receiver_compiles": ("REJECT", _CLOSURE_RECEIVER_REASON),
    "meet_closure_readonly_inferred": ("REJECT", _CLOSURE_RECEIVER_REASON),
}
for _name in ("ptr_dll_prev", "ptr_hop4_call", "ptr_hop4_store", "ptr_hop6_call",
              "ptr_inline_vs_ptr", "ptr_parent", "ptr_rc", "ptr_recursive_optional",
              "ptr_ring", "ptr_second_param", "ptr_selfloop_call",
              "ptr_selfloop_ptrfact", "ptr_selfloop_store",
              "ptr_two_types_linked_call", "ptr_walk_samefield_other",
              "reuse_cross_loops", "reuse_loopvar_types"):
    ACCEPTED[_name] = (UN, "BUGS.md#pointer-structure-aliases-unmodelled")
ACCEPTED["ptr_box"] = (UN, "BUGS.md#pointer-structure-aliases-unmodelled (truth stays-value: Box is unique ownership, so the model's kill is the conservative one)")
for _name, _slug in (
        ("unm_two_params", "may-hold-relation-unmodelled-shapes (1)"),
        ("unm_nested_alias_inner", "may-hold-relation-unmodelled-shapes (2)"),
        ("unm_subscript_local", "subscript-element-not-keyed"),
        ("unm_property_setter", "property-setter-sibling-write-invisible-to-loop-kill-set (the straight-line face)"),
        ("unm_match_kw_capture", "borrowed-origin-not-related-to-source (a keyword capture)"),
        ("unm_iadd_local", "iadd-target-keeps-facts-beneath"),
        ("res_recv_walk", "mutating-call-walk-keeps-linked-local-fact"),
        ("res_ptr_param_root", "may-hold-relation-unmodelled-shapes (3)"),
        ("res_ptr_call_root", "may-hold-relation-unmodelled-shapes (3)"),
        ("res_back_edge", "may-hold-relation-unmodelled-shapes"),
        ("res_pending_deref", "subexpression-right-to-left-eval"),
        ("res_two_loopvars", "subscript-element-not-keyed"),
        ("res_bare_borrow", "field-loan-whole-record-callee-unchecked")):
    ACCEPTED[_name] = (UN, "BUGS.md#" + _slug)


def generate(out: Path) -> list[Path]:
    """Write every semantic row under `out` and return the files."""
    global OUT
    OUT = out
    main()
    return sorted(p for p in OUT.glob("*.py") if not p.name.startswith("cost_"))


def main() -> None:
    spelling_rows()
    OUT.mkdir(parents=True, exist_ok=True)
    names = {r[0] for r in ROWS}
    assert set(ACCEPTED) <= names, sorted(set(ACCEPTED) - names)
    for name, group, expected, notes, verdict, desc, src in ROWS:
        head = [f"# {desc}", f"# GROUP: {group}", f"# EXPECTED: {expected}"]
        if name in ACCEPTED:
            got, why = ACCEPTED[name]
            head.append(f"# ACCEPTED: {got} -- {why}")
        if verdict != "local":
            head.append(f"# VERDICT: {verdict}")
        head += [f"# NOTE: {n}" for n in notes]
        text = "\n".join(head) + "\n" + src.strip("\n").lstrip("\n") + "\n\n\nmain()\n"
        # imports must stay first after the header; preambles starting with
        # `from` already do
        (OUT / f"{name}.py").write_text(text)
    print(len(ROWS), "rows")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        OUT = Path(sys.argv[1]).resolve()
    main()
    print(OUT)
