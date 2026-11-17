# MIR's "may" warnings, continued: element and dict-value shapes that stay silent, and the known
# limits (false "may" warnings on correlated conditions or after a call that always raises,
# silent dangles read under the store's own condition, a definite warning through two parameters
# that may be one object).
from fixtures import Hs, Tok, fail, mk
from tpy import StrView, int32


# value element, safe: the str loop variable owns its copy, so a store of an element under the loop frees nothing it holds
def value_element(a: list[str]) -> None:
    for s in a:
        a[0] = mk("w")  # tpyc: ok
        print("value_element", s)


# dict values, safe: storing an existing key under a walk of the values replaces in place, inserting nothing
def values_existing_key(d: dict[str, str]) -> None:
    for v in d.values():
        d["a"] = mk("v")  # tpyc: ok
        print("values_existing_key", v)


# known false warning: BUGS.md#mir-may-warning-correlated-conditions -- a write and a later read through
# the same joined name: `x.s = "safe"` may rewrite either object, so the read of `x.s` may see the old buffer
def alias_reset_read(f: bool) -> None:
    buf = mk("a")
    a = Tok(buf, 1)
    b = Tok(buf, 2)
    x = a if f else b
    buf = mk("z")  # tpyc: warning(/this assignment frees the old value of 'buf', and 'a.s' may still view it and is read on line 32/) warning(/this assignment frees the old value of 'buf', and 'b.s' may still view it and is read on line 32/)
    x.s = "safe"
    print("alias_reset_read", x.s[0])


# known false warning: BUGS.md#mir-may-warning-correlated-conditions -- the copy twin of alias_reset_read:
# `y = x` after the write reads the new value whichever object `x` is
def alias_copy_read(f: bool) -> None:
    buf = mk("a")
    a = Tok(buf, 1)
    b = Tok(buf, 2)
    x = a if f else b
    buf = mk("z")  # tpyc: warning(/this assignment frees the old value of 'buf', and 'a.s' may still view it and is read on line 45/) warning(/this assignment frees the old value of 'buf', and 'b.s' may still view it and is read on line 45/)
    x.s = "safe"
    y = x
    print("alias_copy_read", y.s[0])


# known false warning: BUGS.md#mir-may-warning-correlated-conditions -- the second `if f:` rebuilds the
# record whenever the first one made it view `buf`, but paths are not correlated
def correlated_scope_rebind(f: bool) -> str:
    tk = Tok("x", 0)
    if f:
        buf = mk("g")
        tk = Tok(buf, 1)  # tpyc: warning(/'buf' is freed at the end of this block, and 'tk.s' may still view it and is read on line 57/)
    if f:
        tk = Tok("y", 2)
    return str(tk.s[0])


# known false warning: BUGS.md#mir-warning-after-always-raising-call -- fail() raises on every path,
# so the read after it never runs
def may_after_raise(flag: bool) -> None:
    h = Hs()
    if flag:
        buf = mk("a")
        h.s = buf  # tpyc: warning(/'buf' is freed at the end of this block, and 'h.s' may still view it and is read on line 68/)
    fail()
    print("may_after_raise", h.s[0])


# known silent dangle: BUGS.md#mir-hazard-read-on-some-paths-silent -- the read runs under the store's own
# condition, so not every path from the block's end reads `h.s` (called with False only)
def g_samef(f: bool) -> None:
    h = Hs()
    if f:
        buf = mk("b")
        h.s = buf  # tpyc: ok
    junk = mk("z")
    if f:
        print("g_samef", h.s[0], len(junk))


# known silent dangle: BUGS.md#mir-hazard-read-on-some-paths-silent -- the constructor twin of g_samef
def g_samef_ctor(f: bool) -> None:
    tk = Tok("x", 0)
    if f:
        buf = mk("c")
        tk = Tok(buf, 1)  # tpyc: ok
    junk = mk("z")
    if f:
        print("g_samef_ctor", tk.s[0], len(junk))


class R:
    buf: str
    s: StrView

    def __init__(self, buf: str, s: StrView) -> None:
        self.buf = buf
        self.s = s


# known overstated warning: BUGS.md#mir-strong-write-aliased-parameter-definite -- `q` may be `p`, whose
# reset `p.s = "lit"` then resets `q.s` too, so the hazard holds only maybe (called with one object twice)
def two(p: R, q: R) -> None:
    w: StrView = p.buf
    q.s = w
    p.s = "lit"
    p.buf = mk("five")  # tpyc: warning(/this assignment frees the old value of 'p.buf', but 'q.s' still views it and is read on line 110/)
    print("two", q.s[0])


# known overstated warning: BUGS.md#mir-strong-write-aliased-parameter-definite -- the loop twin of `two`,
# a store escape: `q` may be `p`, whose reset then ends the store (called with one object twice)
def two_loop(p: R, q: R, k: int32) -> None:
    for i in range(k):
        buf = mk(str(i))
        q.s = buf  # tpyc: warning(/'buf' is freed when 'two_loop' returns, but 'q.s' keeps a view of it/)
        p.s = "lit"
    print("two_loop", q.s[0])


def main() -> None:
    shared = R(mk("p"), "x")
    two(shared, shared)
    two_loop(shared, shared, 2)
    g_samef(False)
    g_samef_ctor(False)
    value_element(["one", "two"])
    values_existing_key({"a": "x", "b": "y"})
    alias_reset_read(True)
    alias_reset_read(False)
    alias_copy_read(True)
    alias_copy_read(False)
    print("correlated_scope_rebind", correlated_scope_rebind(True), correlated_scope_rebind(False))
    try:
        may_after_raise(True)
    except IndexError:
        print("may_after_raise raised")
