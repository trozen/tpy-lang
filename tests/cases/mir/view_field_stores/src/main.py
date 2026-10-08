# MIR over stores into view members: a write of a view member, a callee storing a loan
# (a transfer), member records holding a view, owned results and Own parameters of them.
# A conflict section replaces the source, or stores a loan that escapes, only on a path the
# run does not take (`flag` is False); sema is silent on every one of them.
from tpy import Own, StrView, int32, copy


class Tok:
    s: StrView
    n: int32

    def __init__(self, s: str, n: int32) -> None:
        self.s = s
        self.n = n

    # method: stores its argument's loan in the receiver (a published transfer)
    def reset(self, s: str) -> None:  # tpyc: mir(covered) mir_summary(known)
        self.s = s  # tpyc: mir_borrows(self.s, self.s|s)

    # method: a conditional store is a may-store
    def maybe(self, s: str, f: bool) -> None:  # tpyc: mir(covered) mir_summary(known)
        if f:
            self.s = s


class Outer:
    inner: Tok
    tag: int32

    # constructor: a member record holding a view, copied from the lent argument
    def __init__(self, inner: Tok, tag: int32) -> None:  # tpyc: mir(covered)
        self.inner = copy(inner)
        self.tag = tag


class Stamp:
    s: StrView
    n: int32

    # constructor: a view member written in the body, after member initialization
    def __init__(self, s: str, n: int32) -> None:  # tpyc: mir(covered)
        self.s = "lit"
        self.n = n
        if n > 0:
            self.s = s  # tpyc: mir_borrows(self.s, self.s|s)


class R:
    buf: str
    s: StrView

    def __init__(self, buf: str, s: StrView) -> None:
        self.buf = buf
        self.s = s

    # method: an owned-field setter on a record holding a view stores no loan; the caller's
    # own stored loan is the caller's to check (through the published write)
    def set_buf(self, k: int32) -> None:  # tpyc: mir(covered) mir_summary(known)
        self.buf = mk(k)


class Buf:
    text: str

    def __init__(self, text: str) -> None:
        self.text = text


def mk(k: int32) -> str:
    return "x" * (40 + k)


# free function: the callee stored b's loan in t; b is replaced while t lives
# (BUGS.md#record-view-member-source-replaced)
def retained_by_callee(k: int32, flag: bool) -> None:  # tpyc: mir(conflict /replacement/)
    a = mk(k)
    b = mk(k + 1)
    t = Tok(a, 1)
    t.reset(b)  # tpyc: mir_borrows(t.s, a|b)
    if flag:
        b = mk(k + 2)
    print("retained_by_callee", t.s)


# free function: a may-store still retains b (the caller unions, never replaces)
def maybe_retained(k: int32, flag: bool) -> None:  # tpyc: mir(conflict /replacement/)
    a = mk(k)
    b = mk(k + 1)
    t = Tok(a, 1)
    t.maybe(b, flag)  # tpyc: mir_borrows(t.s, a|b)
    if flag:
        b = mk(k + 2)
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
        b = mk(k + 2)
    print("direct_write_source", t.s)


# free function: a body-local buffer stored into the caller's record escapes the call
# (BUGS.md#record-view-field-escapes-local-buffer)
def fill(t: Tok, k: int32) -> None:  # tpyc: mir(conflict /store_escape/) mir_summary(opaque /^summary stores a loan of the body's storage$/)
    buf = mk(k)
    t.s = buf


# free function: a literal's static storage stored into the caller's record
def fill_static(t: Tok) -> None:  # tpyc: mir(covered) mir_summary(known)
    t.s = "lit"  # tpyc: mir_borrows(t.s, t.s|static)


# free function: the caller joins the static transfer with what t held
def static_filled(k: int32) -> int32:  # tpyc: mir(covered) mir_summary(known)
    a = mk(k)
    t = Tok(a, 1)
    fill_static(t)  # tpyc: mir_borrows(t.s, a|static)
    return len(t.s)


# free function: a body-local buffer stored per iteration; its scope ends at the back edge,
# a non-terminal edge, while the caller's record still holds it
def loop_store(t: Tok, n: int32) -> None:  # tpyc: mir(conflict /scope_end/) mir_summary(opaque /^summary stores a loan of the body's storage$/)
    for i in range(n):
        buf = mk(i)
        t.s = buf


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
        a = mk(k + 1)
    print("outer_replaced", o.inner.s)


# free function: an owned result whose member holds the parameter's loan
def make(s: str) -> Own[Tok]:  # tpyc: mir(covered) mir_summary(known)
    return Tok(s, 2)


# free function: the result of make holds b's loan; b is replaced while t lives
# (BUGS.md#record-view-member-source-replaced)
def made_replaced(k: int32, flag: bool) -> None:  # tpyc: mir(conflict /replacement/)
    b = mk(k)
    t = make(b)  # tpyc: mir_borrows(t.s, b)
    if flag:
        b = mk(k + 1)
    print("made_replaced", t.s)


# free function: an owned result whose member views a body-local buffer
# (BUGS.md#record-view-field-escapes-local-buffer)
def make_local(k: int32) -> Own[Tok]:  # tpyc: mir(conflict /store_escape/)
    buf = mk(k)
    return Tok(buf, 3)


# free function: the result keeps the argument's own stored loan on the path that does not store
def maybe_own(r: Own[Tok], s: str, f: bool) -> Own[Tok]:  # tpyc: mir(covered) mir_summary(known)
    if f:
        r.s = s
    return r


# free function: t holds b.text's loan through maybe_own's result; b.text is replaced while t lives
# (BUGS.md#record-view-member-source-replaced)
def own_result_seed(k: int32, flag: bool) -> None:  # tpyc: mir(conflict /replacement/)
    b = Buf(mk(k))
    t = maybe_own(Tok(b.text, 1), "lit", flag)
    if flag:
        b.text = mk(k + 1)
    # the call binds t in a block after the call's own, so the loans are read here
    print("own_result_seed", t.s)  # tpyc: mir_borrows(t.s, b.text|static)


# free function: the same through a member record of the handed-over record
def maybe_outer(o: Own[Outer], s: str, f: bool) -> Own[Outer]:  # tpyc: mir(covered) mir_summary(known)
    if f:
        o.inner.s = s
    return o


# free function: o.inner holds b.text's loan through maybe_outer's result
# (BUGS.md#record-view-member-source-replaced)
def own_member_seed(k: int32, flag: bool) -> None:  # tpyc: mir(conflict /replacement/)
    b = Buf(mk(k))
    o = maybe_outer(Outer(Tok(b.text, 1), 2), "lit", flag)
    if flag:
        b.text = mk(k + 1)
    print("own_member_seed", o.inner.s)


# free function: an Own parameter returned as it came keeps its stored loan
def ident(r: Own[Tok]) -> Own[Tok]:  # tpyc: mir(covered) mir_summary(known)
    return r


# free function: t holds b.text's loan through ident's result
# (BUGS.md#record-view-member-source-replaced)
def ident_kept(k: int32, flag: bool) -> None:  # tpyc: mir(conflict /replacement/)
    b = Buf(mk(k))
    t = ident(Tok(b.text, 1))
    if flag:
        b.text = mk(k + 1)
    print("ident_kept", t.s)  # tpyc: mir_borrows(t.s, b.text)


# free function: an owned result built in the body views s, which may view b.text, replaced before the return
# (BUGS.md#record-view-member-source-replaced)
def make_replace(s: str, b: Buf, flag: bool) -> Own[Tok]:  # tpyc: mir(conflict /replacement/)
    t = Tok(s, 1)
    if flag:
        b.text = mk(4)
    return t


# free function: the call replaces b.text and fills t with its loan
# (BUGS.md#record-view-member-source-replaced)
def made_replace(k: int32, flag: bool) -> None:  # tpyc: mir(conflict /replacement/)
    b = Buf(mk(k))
    t = make_replace(b.text, b, flag)
    print("made_replace", t.s)


# free function: an Own parameter's stored loans come from the caller
# (only `t.s` is read, so the handed-over record is never consumed: the warning is right)
def consume(t: Own[Tok]) -> int32:  # tpyc: mir(covered) mir_summary(known) warning(/never consumed/)
    return len(t.s)


# free function: a body-local buffer stored into an Own parameter's object escapes the call
# (BUGS.md#record-view-field-escapes-local-buffer)
# the Own parameter is only read or written in place, never handed on: the warning is right
def own_store_local(t: Own[Tok], k: int32) -> int32:  # tpyc: mir(conflict /store_escape/) mir_summary(opaque /^summary stores a loan of the body's storage$/) warning(/never consumed/)
    buf = mk(k)
    t.s = buf
    return t.n


# free function: a parameter's loan stored into an Own parameter's object is no transfer:
# the caller never reads its temporary again
# the Own parameter is only read or written in place, never handed on: the warning is right
def own_store_param(t: Own[Tok], s: str) -> int32:  # tpyc: mir(covered) mir_summary(known) warning(/never consumed/)
    t.s = s
    return t.n


# free function: a record built for the call is handed to the Own parameter
def consume_built(k: int32) -> int32:  # tpyc: mir(covered) mir_summary(known)
    a = mk(k)
    return consume(Tok(a, 1))


# free function: a named record handed to an Own parameter
def consume_named(k: int32) -> int32:  # tpyc: mir(uncovered /^unsupported record argument$/)
    t = Tok("static", k)
    return consume(t)


# free function: a replacement through a lent parameter may reach what the Own parameter's object views
# (BUGS.md#record-view-member-source-replaced)
# the Own parameter is only read or written in place, never handed on: the warning is right
def change(a: R, b: Own[R], flag: bool) -> int32:  # tpyc: mir(conflict /replacement/) mir_summary(known) warning(/never consumed/)
    if flag:
        a.buf = mk(5)
    return len(b.s)


# free function: one named record lent and handed over stays refused, which keeps an Own
# parameter's object private to the callee
def private_own_alias(k: int32) -> int32:  # tpyc: mir(uncovered /^unsupported record argument$/)
    r = R(mk(k), "static")
    v: StrView = r.buf
    r.s = v
    # a named record at an Own parameter is copied, as the warning says; the copy keeps the callee's buffer its own
    return change(r, r, False)  # tpyc: warning(/copies R/)


# free function: a store into t beside a replacement through an Own parameter
# the Own parameter is only read or written in place, never handed on: the warning is right
def store_replace(t: Tok, s: str, b: Own[Buf], flag: bool) -> None:  # tpyc: mir(covered) mir_summary(known) warning(/never consumed/)
    t.s = s
    if flag:
        b.text = mk(6)


# free function: the Own argument is a named record, so the call stays refused
def own_write_transfer(k: int32, flag: bool) -> None:  # tpyc: mir(uncovered /^unsupported record argument$/)
    b = Buf(mk(k))
    t = Tok("static", 1)
    # a named record at an Own parameter is copied, as the warning says; the copy keeps the callee's buffer its own
    store_replace(t, b.text, b, flag)  # tpyc: warning(/copies Buf/)
    print("own_write_transfer", t.s)


class Wrap:
    t: Tok

    def __init__(self, t: Own[Tok]) -> None:
        self.t = t


# free function: a named record's copy handed to an Own constructor parameter
def wrap_copy(t: Tok) -> int32:  # tpyc: mir(uncovered /^handed-over record holds a borrow$/)
    w = Wrap(copy(t))
    return w.t.n


# free function: the callee stores s in t, then replaces b.text, which s may view
# (BUGS.md#record-view-member-source-replaced)
def store_and_replace(t: Tok, s: str, b: Buf, flag: bool) -> None:  # tpyc: mir(conflict /replacement/)
    t.s = s
    if flag:
        b.text = mk(3)


# free function: the caller half: the call replaces b.text, whose loan it stores in t
# (BUGS.md#record-view-member-source-replaced)
def transfer_replace(flag: bool) -> None:  # tpyc: mir(conflict /replacement/)
    b = Buf(mk(2))
    t = Tok("static", 1)
    store_and_replace(t, b.text, b, flag)
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
        s = mk(6)
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
        buf = mk(8)
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
        buf = store_return(t, buf)
    print("call_fill", t.s, len(buf))


# free function: the call stores b.text's loan in t, and its result replaces b.text
# (BUGS.md#record-view-member-source-replaced)
def call_fill_other(k: int32, flag: bool) -> None:  # tpyc: mir(conflict /replacement/)
    b = Buf(mk(k))
    t = Tok("static", 1)
    if flag:
        b.text = store_return(t, b.text)
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
        r = R(mk(12), v)
        print("reused_construct", r.s)
        v = r.buf
        r.s = "static"
    print("reused_construct", len(r.buf))


# free function: a constructor with body effects is not constructed through
def stamped(s: str) -> int32:  # tpyc: mir(uncovered /^constructor body effects$/)
    st = Stamp(s, 1)
    return st.n


# free function: an in-place reseat whose source is the record it replaces
def reseat_self(k: int32, flag: bool) -> None:  # tpyc: mir(uncovered /^in-place replacement holds a borrow$/)
    r = R(mk(k), "static")
    if flag:
        r = R(mk(k + 1), r.buf)
    print("reseat_self", r.s)


class Holder:
    inner: R

    def __init__(self, inner: R) -> None:
        self.inner = copy(inner)


# free function: a whole member holding a view replaced by a record viewing the member it replaces
def member_self(k: int32, flag: bool) -> None:  # tpyc: mir(uncovered /^record member replacement holds a borrow$/)
    o = Holder(R(mk(k), "static"))
    if flag:
        o.inner = R(mk(k + 1), o.inner.buf)
    print("member_self", o.inner.s)


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
    print("make", make("m").s)
    made_replaced(1, False)
    if flag:
        print("make_local", make_local(1).n)
    own_result_seed(1, False)
    own_member_seed(1, False)
    ident_kept(1, False)
    made_replace(1, False)
    print("consume", consume(Tok("r", 3)))
    if flag:
        print("own_store_local", own_store_local(Tok("o", 4), 1))
    print("own_store_param", own_store_param(Tok("o", 5), "p"))
    print("consume_built", consume_built(1))
    print("consume_named", consume_named(2))
    print("private_own_alias", private_own_alias(1))
    own_write_transfer(1, False)
    print("wrap_copy", wrap_copy(w))
    b = Buf(mk(2))
    store_and_replace(t, b.text, b, False)
    print("store_and_replace", t.s)
    transfer_replace(False)
    alias_result(False)
    borrowed_result(False)
    n = put(w, "p")
    print("put", n, w.s)
    eager(False)
    call_fill(False)
    call_fill_other(1, False)
    call_fill_control(1)
    reused_construct(1)
    print("stamped", stamped("st"))
    reseat_self(1, False)
    member_self(1, False)


main(False)
