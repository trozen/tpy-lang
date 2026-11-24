# MIR over calls that store into a record beside a replacement: published preconditions, results
# resolved after the callee's store, and constructors with body effects.
from dataclasses import dataclass
from fixtures import Buf, R, Stamp, Tok, mk
from part_own import Wrap
from tpy import Own, StrView, copy, int32


@dataclass(frozen=True)
class Frozen:
    s: StrView
    n: int32


# free function: a frozen dataclass's view and scalar fields, initialized by its constructor
def frozen_fields(s: str) -> int32:  # tpyc: mir(covered)
    f = Frozen(s, 2)
    return len(f.s) + f.n  # tpyc: mir_borrows(f.s, s)


# free function: a named record moved into an Own constructor parameter; `t.s` views the temporary
# `mk(k)`, dead after its statement while `w` keeps it: BUGS.md#record-view-field-escapes-local-buffer
def wrap_move(k: int32) -> int32:  # tpyc: mir(conflict /scope_end/)
    t = Tok(mk(k), 1)
    w = Wrap(t)
    return w.t.n


# free function: the callee stores s in t, then replaces b.text, which s may view: a precondition the
# body publishes, checked at callers MIR analyzes (BUGS.md#record-view-member-source-replaced)
def store_and_replace(t: Tok, s: str, b: Buf, flag: bool) -> None:  # tpyc: mir(conflict /alias_obligation/)
    t.s = s
    if flag:
        b.text = mk(3)


# free function: the caller half: the call replaces b.text, whose loan it stores in t, so passing
# b.text beside b is warned at the call (BUGS.md#record-view-member-source-replaced)
def transfer_replace(flag: bool) -> None:  # tpyc: mir(conflict /replacement/)
    b = Buf(mk(2))
    t = Tok("static", 1)
    store_and_replace(t, b.text, b, flag)  # tpyc: warning(/argument 'b.text' is what 'store_and_replace' modifies/) warning(/this call frees the old value of 'b.text', and 't.s' may still view it/)
    print("transfer_replace", t.s)


# free function: write_copy may be called with a is b; its result copies b after the store
def write_copy(a: Tok, b: Tok, s: str) -> Own[Tok]:  # tpyc: mir(covered) mir_summary(known)
    a.s = s
    return copy(b)


# free function: u holds s through the store into t, which the result copies
# (BUGS.md#record-view-member-source-replaced)
def alias_result(flag: bool) -> None:  # tpyc: mir(conflict /replacement/)
    t = Tok("static", 1)
    s = mk(5)
    u = write_copy(t, t, s)  # tpyc: mir_borrows(u.s, static|s)
    t.s = "static"
    if flag:
        s = mk(6)  # tpyc: warning(/this assignment frees the old value of 's', and 'u.s' may still view it/)
    print("alias_result", u.s)


# free function: a borrowed result of b's member, read after a store into a, which may be b
def put_read(a: Tok, b: Tok, s: str) -> StrView:  # tpyc: mir(covered) mir_summary(known)
    a.s = s
    return b.s


# free function: the view result resolves after the callee's store
# (BUGS.md#record-view-member-source-replaced)
def borrowed_result(flag: bool) -> None:  # tpyc: mir(conflict /replacement/)
    t = Tok("static", 1)
    buf = mk(7)
    v: StrView = put_read(t, t, buf)  # tpyc: mir_borrows(v, static|buf)
    # sema keys v's loan on t, so the rebind warns although v views buf (BUGS.md#view-member-rebind-warns-while-borrowed)
    t.s = "static"  # tpyc: warning(/while borrowed/)
    if flag:
        buf = mk(8)  # tpyc: warning(/this assignment frees the old value of 'buf', and 'v' may still view it/)
    print("borrowed_result", v)


def put(t: Tok, s: str) -> int32:  # tpyc: mir(covered) mir_summary(known)
    t.s = s
    return 1


def first(s: StrView, n: int32) -> StrView:
    return s


# free function: an operand read beside a call storing into the same record has no order
# (BUGS.md#subexpression-right-to-left-eval)
def eager(flag: bool) -> None:  # tpyc: mir(uncovered /^order-sensitive eager operands$/)
    t = Tok("static", 1)
    buf = mk(9)
    v: StrView = first(t.s, put(t, buf))
    # C++ evaluates the two operands in either order, so only the length of the loan is stable
    print("eager", len(v) > 0)


def store_return(t: Tok, s: str) -> str:  # tpyc: mir(covered) mir_summary(known)
    t.s = s
    return mk(10)


def keep_return(t: Tok, s: str) -> str:  # tpyc: mir(covered) mir_summary(known)
    return mk(10)


# free function: the call stores buf's loan in t, and its result replaces buf
# (BUGS.md#record-view-member-source-replaced)
def call_fill(flag: bool) -> None:  # tpyc: mir(conflict /replacement/)
    buf = mk(11)
    t = Tok("static", 1)
    if flag:
        buf = store_return(t, buf)  # tpyc: warning(/this assignment frees the old value of 'buf', and 't.s' may still view it/)
    print("call_fill", t.s, len(buf))


# free function: the call stores b.text's loan in t, and its result replaces b.text
# (BUGS.md#record-view-member-source-replaced)
def call_fill_other(k: int32, flag: bool) -> None:  # tpyc: mir(conflict /replacement/)
    b = Buf(mk(k))
    t = Tok("static", 1)
    if flag:
        b.text = store_return(t, b.text)  # tpyc: warning(/this assignment frees the old value of 'b.text', and 't.s' may still view it/)
    print("call_fill_other", t.s)


# free function: a callee that stores nothing leaves its result free to replace buf
def call_fill_control(k: int32) -> None:  # tpyc: mir(covered)
    buf = mk(k)
    t = Tok("static", 1)
    buf = keep_return(t, buf)
    print("call_fill_control", t.s, len(buf))


# free function: the reused backing's new record views the buffer the same write replaces
# (BUGS.md#record-view-member-source-replaced)
def reused_construct(n: int32) -> None:  # tpyc: mir(conflict /replacement/)
    v: StrView = "static"
    anchor = R("anchor", "static")
    r = anchor
    for i in range(n):
        r = anchor
        r = R(mk(12), v)  # tpyc: warning(/this assignment frees the old value of 'r', and 'r.s' may still view it/)
        print("reused_construct", r.s)
        v = r.buf
        r.s = "static"
    print("reused_construct", len(r.buf))


# free function: a constructor with body effects is called through its summary
def stamped(s: str) -> int32:  # tpyc: mir(covered)
    st = Stamp(s, 1)  # tpyc: mir_borrows(st.s, s|static)
    return st.n


# free function: a reseat whose new record views the old record's buffer: the
# old record keeps a slot of its own, so nothing the view reads is replaced
def reseat_self(k: int32, flag: bool) -> None:  # tpyc: mir(covered)
    r = R(mk(k), "static")
    if flag:
        r = R(mk(k + 1), r.buf)
    print("reseat_self", r.s)


def main() -> None:
    print("frozen_fields", frozen_fields("fz"))
    transfer_replace(False)
    alias_result(False)
    borrowed_result(False)
    eager(False)
    call_fill(False)
    call_fill_other(1, False)
    call_fill_control(1)
    reused_construct(1)
    print("stamped", stamped("st"))
    reseat_self(1, False)
    print("wrap_move", wrap_move(1))
