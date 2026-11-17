# MIR's "may" lifetime warnings: a holder that only may keep the freed storage, at block ends,
# in methods and constructors, over joined sources and callees; guarded siblings stay silent.
from fixtures import Hs, Tok, mk
from tpy import Own, Span, StrView, int32


class Hl:
    items: list[int32]

    def __init__(self) -> None:
        self.items = mk_list(1)


def mk_list(n: int32) -> Own[list[int32]]:
    xs: list[int32] = []
    for i in range(24):
        xs.append(n)
    return xs


# may hazard, end of a block, constructor: the record rebuilt in the block views its buffer on one path
def b_ctor(f: bool) -> None:
    tk = Tok("x", 0)
    if f:
        buf = mk("g")
        tk = Tok(buf, 1)  # tpyc: warning(/'buf' is freed at the end of this block, and 'tk.s' may still view it and is read on line 28/)
    junk = mk("z")
    print("b_ctor", tk.s[0], len(junk))


# may hazard, end of a block, constructor in both arms: only the `if` arm views a block-local buffer
def b_ctor_else(f: bool) -> None:
    if f:
        buf = mk("h")
        tk = Tok(buf, 1)  # tpyc: warning(/'buf' is freed at the end of this block, and 'tk.s' may still view it and is read on line 39/)
    else:
        tk = Tok("x", 0)
    junk = mk("z")
    print("b_ctor_else", tk.s[0], len(junk))


# may hazard, end of a block, field store: the view member keeps a block-local buffer on one path
def h_field(f: bool) -> None:
    h = Hs()
    if f:
        buf = mk("b")
        h.s = buf  # tpyc: warning(/'buf' is freed at the end of this block, and 'h.s' may still view it and is read on line 49/)
    junk = mk("z")
    print("h_field", h.s[0], len(junk))


class Sites:
    n: int32

    # may hazard, end of a block, method body with local holders
    def run(self, f: bool) -> None:
        tk = Tok("x", 0)
        if f:
            buf = mk("m")
            tk = Tok(buf, 1)  # tpyc: warning(/'buf' is freed at the end of this block, and 'tk.s' may still view it and is read on line 62/)
        junk = mk("z")
        print("method", tk.s[0], len(junk))

    # may hazard, end of a block, constructor body with local holders
    def __init__(self, f: bool) -> None:
        self.n = 0
        h = Hs()
        if f:
            buf = mk("c")
            h.s = buf  # tpyc: warning(/'buf' is freed at the end of this block, and 'h.s' may still view it and is read on line 72/)
        junk = mk("z")
        print("ctor", h.s[0], len(junk))


# may hazard, end of a loop body left by `break`: the view member keeps the body's buffer
def store_break(xs: list[str]) -> None:
    h = Hs()
    for x in xs:
        buf = mk(x)
        h.s = buf
        break  # tpyc: warning(/'buf' is freed when control leaves its block here, and 'h.s' may still view it and is read on line 83/)
    junk = mk("z")
    print("store_break", h.s[0], len(junk))


# may hazard, iteration over a joined source: growing `a` may move what the walk over `v` walks
def cursor_join(flag: bool) -> None:
    a = [1, 2, 3]
    b = [4, 5, 6]
    v = a if flag else b
    n = 0
    for x in v:
        if n == 0:
            a.append(7)  # tpyc: warning(/this call may move the elements of 'a', and the iteration over 'v' may still walk them and continues on line 92/)
        n += 1
        print("cursor_join", x)


# may hazard, iteration over a joined record's field: replacing `h.items` may free the walked list
def field_cursor(f: bool) -> None:
    h = Hl()
    g = Hl()
    x = h if f else g
    t = 0
    for i in x.items:
        h.items = mk_list(2)  # tpyc: warning(/this assignment frees the old value of 'h.items', and the iteration over 'x.items' may still walk it and continues on line 105/)
        t += i
    print("field_cursor", t)


# may hazard, leaf cursor: a callee may have stored `buf` in `t.s`, whose characters the loop walks
def leaf_cursor(flag: bool) -> None:
    buf = mk("a")
    t = Hs()
    t.maybe(buf, flag)
    n = 0
    for c in t.s:
        if n == 0:
            buf = mk("z")  # tpyc: warning(/this assignment frees the old value of 'buf', and the iteration over 't.s' may still walk it and continues on line 117/)
        if c == "z":
            n += 1
    print("leaf_cursor", n, len(buf))


# may hazard, a rewrite through a joined name after the block: `x.s = ...` may not rebind `a.s`
def may_rebind(c: bool, f: bool) -> None:
    a = Hs()
    b = Hs()
    x = a if c else b
    if f:
        buf = mk("b")
        a.s = buf  # tpyc: warning(/'buf' is freed at the end of this block, and 'a.s' may still view it and is read on line 135/)
    x.s = "safe"
    junk = mk("z")
    print("may_rebind", a.s[0], len(junk))


# may hazard, a Span over a joined slice: growing `a` may move what `s` views
def span_join(c: bool) -> int32:
    a = [1, 2]
    b = [3, 4]
    s: Span[int32] = b[0:]
    if c:
        s = a[0:]
    a.append(5)  # tpyc: warning(/Mutation of 'a' while borrowed \('append' may invalidate references\)/) warning(/this call may move the elements of 'a', and 's' may still view them and is read on line 146/)
    return s[0]


# may hazard, a view copied back into a field: `a.s = v` restores the freed buffer, read through `x`
def restore_field(f: bool) -> None:
    buf = mk("a")
    old = Tok(buf, 0)
    a = Tok(buf, 1)
    b = Tok("safe", 2)
    x = a if f else b
    buf = mk("z")  # tpyc: warning(/this assignment frees the old value of 'buf', and 'old.s' may still view it and is read on line 160/)
    x.s = "safe"
    v: StrView = old.s
    a.s = v
    print("restore_field", x.s[0])


# may hazard, a callee that only may reset: `a.maybe_reset(flag)` leaves the copy in `y.s` viewing the
# freed buffer when flag is False (called with True only)
def callee_may_reset(flag: bool) -> None:
    buf = mk("a")
    old = Hs()
    old.s = buf
    a = Hs()
    y = a
    buf = mk("z")  # tpyc: warning(/this assignment frees the old value of 'buf', and 'old.s' may still view it and is read on line 175/)
    v: StrView = old.s
    y.s = v
    a.maybe_reset(flag)
    print("callee_may_reset", y.s[0])


# guard, safe: the read runs only on the path that stored nothing
def g_notc(c: bool) -> None:
    h = Hs()
    if c:
        buf = mk("a")
        h.s = buf  # tpyc: ok
    junk = mk("z")
    if not c:
        print("g_notc", h.s[0], len(junk))


# guard, safe: the record is rebuilt from a literal before the block ends
def g_rebind(f: bool) -> None:
    tk = Tok("x", 0)
    if f:
        buf = mk("d")
        tk = Tok(buf, 1)
        tk = Tok("y", 2)  # tpyc: ok
    junk = mk("z")
    print("g_rebind", tk.s[0], len(junk))


# guard, safe: the view member is rebound to a literal before the block ends
def g_rebind_field(f: bool) -> None:
    h = Hs()
    if f:
        buf = mk("e")
        h.s = buf
        h.s = "y"  # tpyc: ok
    junk = mk("z")
    print("g_rebind_field", h.s[0], len(junk))


def main() -> None:
    # each warned section with the argument that keeps it safe
    b_ctor(False)
    b_ctor_else(False)
    h_field(False)
    Sites(False).run(False)
    store_break([])
    cursor_join(False)
    field_cursor(False)
    leaf_cursor(False)
    may_rebind(True, True)
    print("span_join", span_join(False))
    restore_field(False)
    callee_may_reset(True)
    g_notc(True)
    g_rebind(True)
    g_rebind_field(True)
