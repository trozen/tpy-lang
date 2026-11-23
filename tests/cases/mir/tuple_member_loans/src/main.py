# MIR over a borrow of a tuple member (`r = t[0]`, `t[0].reset()`, `touch(t[0])`): the member is
# an aggregate source in place, as a record field is. Every section mutates through the borrow
# and prints the tuple's member after it, so CPython's aliasing is observed.
from tpy import copy, int32, readonly

from fixtures import Holder, P, S, bump_base, take_opt, touch
from part_limits import main as limits_main
from part_views import main as views_main


# free function: an owned tuple local's inline member bound to a local
def tup_bind() -> int32:  # tpyc: mir(certified)
    t = (P(1), 2)
    r = t[0]  # tpyc: mir_borrows(r, t[0])
    r.v = 5
    return t[0].v


# free function: the member as a method receiver
def tup_call() -> int32:  # tpyc: mir(covered)
    t = (P(1), 2)
    t[0].reset()
    return t[0].v


# free function: the member as an argument
def tup_arg() -> int32:  # tpyc: mir(covered)
    t = (P(1), 2)
    touch(t[0])
    return t[0].v


# method: the member of a tuple built in the method
class M:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def meth(self) -> int32:  # tpyc: mir(certified)
        t = (P(1), 2)
        r = t[0]
        r.v = 3
        return t[0].v


# constructor: the member of a tuple built in `__init__`, written through a local bound to it
class C:
    n: int32

    def __init__(self) -> None:  # tpyc: mir(certified)
        # MIR lowers a constructor whose fields are all set before its other statements
        # (no default-construction fact: BUGS.md#ctor-demoted-field-default-construct-runs-init).
        self.n = 0
        t = (P(1), 2)
        r = t[0]  # tpyc: mir_borrows(r, t[0])
        r.v = 6
        self.n = t[0].v


# loop: the member bound again each pass
def loop_member(n: int32) -> int32:  # tpyc: mir(certified)
    s = 0
    t = (P(1), 2)
    for i in range(n):
        r = t[0]
        r.v += i
        s += r.v
    return s


# reseat: a local bound to one tuple's member, then to another's
def reseat(p: tuple[P, int32], q: tuple[P, int32]) -> int32:  # tpyc: mir(certified)
    a = p[0]  # tpyc: mir_borrows(a, p[0])
    a = q[0]  # tpyc: mir_borrows(a, q[0])
    a.v = 9
    return q[0].v


# readonly tuple: its member is borrowed readonly (a write through `a` is refused)
def readonly_member(p: readonly[tuple[P, int32]], q: readonly[tuple[P, int32]]) -> int32:  # tpyc: mir(certified)
    a = p[0]
    a = q[0]
    return a.v


# borrow tuple: a local tuple of a parameter holds a pointer member, bound, passed and called on;
# each write is observed through the parameter, so a copied member would change the output
def tup_borrowed(p: P) -> int32:  # tpyc: mir(covered)
    t = (p, 2)  # tpyc: mir_borrows(t[0], p)
    r = t[0]  # tpyc: mir_borrows(r, p)
    r.v = 4
    print("borrowed bound", p.v)
    touch(t[0])
    print("borrowed passed", p.v)
    t[0].reset()
    return p.v


# Optional parameter: the member's address passed as `P | None`
def opt_arg() -> int32:  # tpyc: mir(covered)
    t = (P(1), 2)
    take_opt(t[0])
    return t[0].v


# subclass member at a base parameter
def sub_arg() -> int32:  # tpyc: mir(covered)
    t = (S(1), 2)
    bump_base(t[0])
    return t[0].v


# copy: the member's object copied out, the tuple's own untouched
def copied() -> int32:  # tpyc: mir(covered)
    t = (P(1), 2)
    c = copy(t[0])
    c.v = 7
    return t[0].v


# copy: a borrow tuple's pointer member's object copied out, the parameter untouched
def copied_pointer(p: P) -> int32:  # tpyc: mir(covered)
    t = (p, 2)  # tpyc: mir_borrows(t[0], p)
    c = copy(t[0])
    c.v = 7
    return p.v


# field store: the member's object copied into a field (warned; the copy is intended, so
# only the field is read after the write, and `t[1]` keeps `t` from being moved out of)
def stored(h: Holder) -> int32:  # tpyc: mir(covered)
    t = (P(3), 2)
    h.p = t[0]  # tpyc: warning(/copies P into field/)
    h.p.v = 8
    return h.p.v + t[1]


# The holder outlives the block's tuple: the C++ frees `u` at the end of the
# `if` block while `r` still points into it. Defined, never called.
def tuple_scope(c: bool) -> int32:  # tpyc: mir(conflict /scope_end/)
    t = (P(1), 2)
    r = t[0]
    if c:
        u = (P(4), 5)
        r = u[0]  # tpyc: warning(/freed at the end of this block/)
    return r.v


def main() -> None:
    print("bind", tup_bind())
    print("call", tup_call())
    print("arg", tup_arg())
    print("meth", M().meth())
    print("ctor", C().n)
    print("loop", loop_member(3))
    p, q = P(1), P(2)
    print("reseat", reseat((p, 1), (q, 2)), q.v)
    print("readonly", readonly_member((p, 1), (q, 2)))
    b = P(1)
    got = tup_borrowed(b)
    print("borrowed", got, b.v)
    print("opt", opt_arg())
    print("sub", sub_arg())
    print("copy", copied(), copied_pointer(P(5)))
    print("stored", stored(Holder()))
    views_main()
    limits_main()


main()
