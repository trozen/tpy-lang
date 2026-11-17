# MIR over Own parameters: stored loans handed over, stores into the handed-over object, and
# copies handed to Own constructor and function parameters.
from fixtures import Buf, R, Tok, mk
from part_results import ident
from tpy import Own, StrView, copy, int32


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


# free function: a parameter's loan stored into an Own parameter's object is no transfer: no
# holder of the caller's reads that object after the call (a moved name's MOVE makes one a
# retention conflict at the caller)
# the Own parameter is only read or written in place, never handed on: the warning is right
def own_store_param(t: Own[Tok], s: str) -> int32:  # tpyc: mir(covered) mir_summary(known) warning(/never consumed/)
    t.s = s
    return t.n


# free function: a record built for the call is handed to the Own parameter
def consume_built(k: int32) -> int32:  # tpyc: mir(covered) mir_summary(known)
    a = mk(k)
    return consume(Tok(a, 1))


# free function: a named record handed to an Own parameter
def consume_named(k: int32) -> int32:  # tpyc: mir(covered)
    t = Tok("static", k)
    return consume(t)


# free function: a replacement through a lent parameter may reach what the Own parameter's object views
# (BUGS.md#record-view-member-source-replaced)
# the Own parameter is only read or written in place: the warning is right; its view is the caller's choice (opaque summary)
def change(a: R, b: Own[R], flag: bool) -> int32:  # tpyc: mir(conflict /replacement/) mir_summary(opaque /summary retention/) warning(/never consumed/)
    if flag:
        a.buf = mk(5)
    return len(b.s)


# free function: one named record lent and handed over stays refused, which keeps an Own
# parameter's object private to the callee: the call refuses first at the callee's opaque
# summary; behind it, the handed-over argument is the temporary sema's copy builds, which no
# owned argument form admits
def private_own_alias(k: int32) -> int32:  # tpyc: mir(uncovered /^call needs finalized known summary$/)
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
def own_write_transfer(k: int32, flag: bool) -> None:  # tpyc: mir(uncovered /^constructor argument type$/)
    b = Buf(mk(k))
    t = Tok("static", 1)
    # a named record at an Own parameter is copied, as the warning says; the copy keeps the callee's buffer its own
    store_replace(t, b.text, b, flag)  # tpyc: warning(/copies Buf/)
    print("own_write_transfer", t.s)


class Wrap:
    t: Tok

    def __init__(self, t: Own[Tok]) -> None:
        self.t = t


# free function: a named record's copy handed to an Own constructor parameter carries its stored loan
def wrap_copy(t: Tok) -> int32:  # tpyc: mir(covered)
    w = Wrap(copy(t))
    return w.t.n


# free function: the copy views b.text, which is replaced while the wrapper lives
# (BUGS.md#record-view-member-source-replaced)
def wrap_copy_replaced(k: int32, flag: bool) -> None:  # tpyc: mir(conflict /replacement/)
    b = Buf(mk(k))
    t = Tok(b.text, 1)
    w = Wrap(copy(t))
    if flag:
        b.text = mk(k + 1)  # tpyc: warning(/this assignment frees the old value of 'b.text'/)
    print("wrap_copy_replaced", w.t.s, t.n)


# free function: a named record's copy handed to an Own function parameter carries its stored loan
# into the owned result
def factory_copy(k: int32) -> int32:  # tpyc: mir(covered)
    a = mk(k)
    t = Tok(a, 1)
    u = ident(copy(t))
    # the call binds u in a block after the call's own, so the loans are read here
    return len(u.s) + t.n  # tpyc: mir_borrows(u.s, a)


def main(flag: bool) -> None:
    print("consume", consume(Tok("r", 3)))
    if flag:
        print("own_store_local", own_store_local(Tok("o", 4), 1))
    print("own_store_param", own_store_param(Tok("o", 5), "p"))
    print("consume_built", consume_built(1))
    print("consume_named", consume_named(2))
    print("private_own_alias", private_own_alias(1))
    own_write_transfer(1, False)
    print("factory_copy", factory_copy(1))
    wrap_copy_replaced(1, False)
