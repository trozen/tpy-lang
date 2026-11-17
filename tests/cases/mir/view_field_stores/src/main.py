# MIR over stores into view members and records written through a place: the core shapes are here;
# owned results, Own parameters, calls, whole members and loops are in the part_* modules.
import part_calls, part_loops, part_members, part_own, part_results
from fixtures import Buf, Outer, R, Tok, mk
from tpy import StrView, int32


# free function: the callee stored b's loan in t; b is replaced while t lives
# (BUGS.md#record-view-member-source-replaced)
def retained_by_callee(k: int32, flag: bool) -> None:  # tpyc: mir(conflict /replacement/)
    a = mk(k)
    b = mk(k + 1)
    t = Tok(a, 1)
    t.reset(b)  # tpyc: mir_borrows(t.s, a|b)
    if flag:
        b = mk(k + 2)  # tpyc: warning(/this assignment frees the old value of 'b', and 't.s' may still view it/)
    print("retained_by_callee", t.s)


# free function: a may-store still retains b (the caller unions, never replaces)
def maybe_retained(k: int32, flag: bool) -> None:  # tpyc: mir(conflict /replacement/)
    a = mk(k)
    b = mk(k + 1)
    t = Tok(a, 1)
    t.maybe(b, flag)  # tpyc: mir_borrows(t.s, a|b)
    if flag:
        b = mk(k + 2)  # tpyc: warning(/this assignment frees the old value of 'b', and 't.s' may still view it/)
    print("maybe_retained", t.s)


# free function: a write into the one local record replaces its loan, so a is free
def direct_write(k: int32) -> None:  # tpyc: mir(covered)
    a = mk(k)
    b = mk(k + 1)
    t = Tok(a, 1)
    t.s = b  # tpyc: mir_borrows(t.s, b)
    a = mk(k + 2)
    print("direct_write", t.s, a)


# free function: after the write the record holds b
def direct_write_source(k: int32, flag: bool) -> None:  # tpyc: mir(conflict /replacement/)
    a = mk(k)
    b = mk(k + 1)
    t = Tok(a, 1)
    t.s = b
    if flag:
        b = mk(k + 2)  # tpyc: warning(/this assignment frees the old value of 'b'/)
    print("direct_write_source", t.s)


# free function: a body-local buffer stored into the caller's record escapes the call
# (BUGS.md#record-view-field-escapes-local-buffer)
def fill(t: Tok, k: int32) -> None:  # tpyc: mir(conflict /store_escape/) mir_summary(opaque /^summary stores a loan of the body's storage$/)
    buf = mk(k)
    t.s = buf  # tpyc: warning(/'buf' is freed when 'fill' returns/)


# free function: a literal's static storage stored into the caller's record, replacing the caller's loan
def fill_static(t: Tok) -> None:  # tpyc: mir(covered) mir_summary(known)
    t.s = "lit"  # tpyc: mir_borrows(t.s, static)


# free function: the caller joins the static transfer with what t held
def static_filled(k: int32) -> int32:  # tpyc: mir(covered) mir_summary(known)
    a = mk(k)
    t = Tok(a, 1)
    fill_static(t)  # tpyc: mir_borrows(t.s, a|static)
    return len(t.s)


# free function: a body-local buffer stored per iteration, ending at the back edge while the caller's
# record still holds it; each pass's store renews the escape, so the last one reaches the return
def loop_store(t: Tok, n: int32) -> None:  # tpyc: mir(conflict /scope_end/) mir_summary(opaque /^summary stores a loan of the body's storage$/)
    for i in range(n):
        buf = mk(i)
        t.s = buf  # tpyc: warning(/'buf' is freed when 'loop_store' returns, but 't.s' keeps a view of it/)


# free function: a loan of a member of the caller's storage has no transfer form
def store_member(t: Tok, r: R) -> None:  # tpyc: mir(covered) mir_summary(opaque /^summary transfer source is a member or element$/)
    v: StrView = r.buf
    t.s = v


G = mk(0)


# free function: a global's loan stored into the caller's record has no transfer form
def store_global(t: Tok) -> None:  # tpyc: mir(covered) mir_summary(opaque /^summary transfer source outside the parameters$/)
    t.s = G


# free function: a member record's loan, keyed under the outer object
def outer_read(k: int32) -> int32:  # tpyc: mir(covered) mir_summary(known)
    a = mk(k)
    o = Outer(Tok(a, 1), 2)
    return len(o.inner.s)


# free function: the member record's source is replaced while o lives
# (BUGS.md#record-view-member-source-replaced)
def outer_replaced(k: int32, flag: bool) -> None:  # tpyc: mir(conflict /replacement/)
    a = mk(k)
    o = Outer(Tok(a, 1), 2)
    if flag:
        a = mk(k + 1)  # tpyc: warning(/this assignment frees the old value of 'a'/)
    print("outer_replaced", o.inner.s)


def main(flag: bool) -> None:
    retained_by_callee(1, False)
    maybe_retained(1, False)
    direct_write(1)
    direct_write_source(1, False)
    t = Tok("q", 1)
    fill_static(t)
    print("fill_static", t.s)
    print("static_filled", static_filled(1))
    w = Tok("w", 2)
    if flag:
        fill(w, 1)
        loop_store(w, 2)
    print("fill", w.n)
    r = R("member", "static")
    store_member(w, r)
    print("store_member", w.s)
    store_global(w)
    print("store_global", len(w.s))
    r.set_buf(3)
    print("set_buf", r.buf)
    print("outer_read", outer_read(1))
    outer_replaced(1, False)
    # the sections below share w and t with the ones above
    print("wrap_copy", part_own.wrap_copy(w))
    b = Buf(mk(2))
    part_calls.store_and_replace(t, b.text, b, False)
    print("store_and_replace", t.s)
    n = part_calls.put(w, "p")
    print("put", n, w.s)
    part_results.main(flag)
    part_own.main(flag)
    part_calls.main()
    part_members.main(flag)
    part_loops.main()


main(False)
