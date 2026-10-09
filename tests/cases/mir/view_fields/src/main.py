# MIR over records with StrView members: a view member stores its source's loan in the record object.
# A conflict section replaces the source only on a path the run does not take (`flag` is False).
from tpy import Own, StrView, int32


class Tok:
    s: StrView
    n: int32

    # constructor: a view member stores the loan the lent str parameter holds
    def __init__(self, s: str, n: int32) -> None:  # tpyc: mir(covered)
        self.s = s  # tpyc: mir_borrowed(self.s)
        self.n = n

    # method: the receiver's stored loan is the result's origin
    def text(self) -> StrView:  # tpyc: mir(covered) mir_summary(known)
        return self.s


class Lit:
    s: StrView
    n: int32

    # constructor: a literal member stores static storage
    def __init__(self, n: int32) -> None:  # tpyc: mir(covered)
        self.s = "lit"  # tpyc: mir_borrowed(self.s)
        self.n = n


class R:
    buf: str
    s: StrView

    def __init__(self, b: str, s: str) -> None:
        self.buf = b
        self.s = s


class Buf:
    buf: str

    def __init__(self, buf: str) -> None:
        self.buf = buf


class Child(Tok):
    pass


def mk(k: int32) -> str:
    return "x" * (4 + k)


def setbuf(b: Buf, k: int32) -> None:
    b.buf = mk(k)


# free function: a callee reading a parameter's stored loan
def size(t: Tok) -> int32:  # tpyc: mir(covered) mir_summary(known)
    return len(t.s)


# free function: a view read through a borrowed record parameter; the result lends it
def first(t: Tok) -> StrView:  # tpyc: mir(covered) mir_summary(known)
    return t.s


# free function: a borrowed record result reaches both arguments' objects
def pick(a: Tok, b: Tok, flag: bool) -> Tok:  # tpyc: mir(certified) mir_summary(known)
    if flag:
        return a
    return b


# free function: the source is replaced while the record, moved to u at its last use, lives
def alias_replaced(k: int32, flag: bool) -> None:  # tpyc: mir(conflict /replacement/)
    buf = mk(k)
    t = Tok(buf, 1)  # tpyc: mir_borrows(t.s, buf)
    u = t  # tpyc: mir_borrows(u.s, buf)
    if flag:
        buf = mk(k + 1)
    print("alias_replaced", u.s)


# free function: an alias keeps the old object's loan when the name is reseated
def copied_rebind(k: int32, flag: bool) -> None:  # tpyc: mir(conflict /replacement/)
    a = mk(k)
    b = mk(k + 1)
    t = Tok(a, 1)
    u = t  # tpyc: mir_borrows(u.s, a)
    # u aliases t's object: the write shows through t
    u.n = 7
    print("copied_rebind", t.n)
    t = Tok(b, 2)  # tpyc: mir_borrows(t.s, b)
    if flag:
        a = mk(k + 2)
    print("copied_rebind", u.s, t.s)


# free function: a parameter's view member may view its sibling field
def sibling_view(p: R, flag: bool) -> None:  # tpyc: mir(conflict /replacement/)
    v: StrView = p.s  # tpyc: mir_borrows(v, p.s)
    if flag:
        p.buf = mk(2)
    print("sibling_view", v)


# free function: an owned copy of the member is no loan
def sibling_copy(p: R) -> None:  # tpyc: mir(covered)
    v = p.s
    p.buf = mk(2)
    print("sibling_copy", v)


# free function: two record parameters may be one object
def aliased_params(a: R, b: R, flag: bool) -> None:  # tpyc: mir(conflict /replacement/)
    v: StrView = a.s  # tpyc: mir_borrows(v, a.s)
    if flag:
        b.buf = mk(3)
    print("aliased_params", v)


# free function: the str argument is a temporary of the declaration
def temporary_source(k: int32, flag: bool) -> None:  # tpyc: mir(conflict /scope_end/)
    t = Tok(mk(k), 1)
    if flag:
        print("temporary_source", t.s)
    print("temporary_source", t.n)


# free function: a literal's static storage outlives every replacement
def static_source(k: int32) -> None:  # tpyc: mir(covered)
    buf = mk(k)
    t = Tok("lit", 1)  # tpyc: mir_borrows(t.s, static)
    buf = mk(k + 1)
    print("static_source", t.s, buf)


# free function: the callees read the loan before its source is replaced
def callee_reads(k: int32) -> int32:  # tpyc: mir(covered) mir_summary(known)
    buf = mk(k)
    t = Tok(buf, 1)
    n = size(t) + len(t.text())
    buf = mk(k + 1)
    return n + len(buf)


# free function: a callee reads the loan after its source is replaced
def callee_reads_late(k: int32, flag: bool) -> int32:  # tpyc: mir(conflict /replacement/)
    buf = mk(k)
    t = Tok(buf, 1)  # tpyc: mir_borrows(t.s, buf)
    if flag:
        buf = mk(k + 1)
        return size(t)
    return t.n


# free function: a callee replaces the field the record views
def callee_replaces(k: int32, flag: bool) -> None:  # tpyc: mir(conflict /replacement/)
    b = Buf(mk(k))
    t = Tok(b.buf, 1)  # tpyc: mir_borrows(t.s, b.buf)
    if flag:
        setbuf(b, k)
    print("callee_replaces", t.s)


# free function: a parameter's stored loan, seeded at entry, read after an external write
def late_read(t: Tok, b: Buf, flag: bool) -> StrView:  # tpyc: mir(conflict /replacement/)
    if flag:
        b.buf = mk(2)
    return t.s


# free function: an alias of a parameter reaches its stored loan
def alias_late(t: Tok, b: Buf, flag: bool) -> StrView:  # tpyc: mir(conflict /replacement/)
    u = t  # tpyc: mir_borrows(u.s, t.s)
    if flag:
        b.buf = mk(2)
    return u.s


# free function: an alias reseated in a loop still reaches the parameter's stored loan
def loop_alias(t: Tok, b: Buf, n: int32, flag: bool) -> None:  # tpyc: mir(conflict /replacement/)
    u = t
    for i in range(n):
        u = t  # tpyc: mir_borrows(u.s, t.s)
    if flag:
        b.buf = mk(2)
    print("loop_alias", u.s)


# free function: either argument's stored loan through a borrowed record result
def pick_late(a: Tok, b: Tok, buf: Buf, flag: bool) -> StrView:  # tpyc: mir(conflict /replacement/)
    t = pick(a, b, flag)  # tpyc: mir_borrows(t.s, a.s|b.s)
    if flag:
        buf.buf = mk(2)
    return t.s


# free function: a view read off a record built in one branch
def detached_view(k: int32, flag: bool) -> None:  # tpyc: mir(conflict /replacement/)
    a = mk(k)
    v: StrView = "lit"
    if flag:
        t = Tok(a, 1)
        v = t.s  # tpyc: mir_borrows(v, a)
    a = mk(k + 1)
    print("detached_view", v)


# free function: a literal member stores static storage
def literal_member() -> int32:  # tpyc: mir(covered) mir_summary(known)
    c = Lit(1)
    return len(c.s) + c.n


# free function: a view member write into the caller's record
def retarget(t: Tok, s: str) -> None:  # tpyc: mir(covered) mir_summary(known)
    t.s = s


# free function: a record holding a view handed over
# (only `t.n` is read, so the handed-over record is never consumed: the warning is right)
def consume(t: Own[Tok]) -> int32:  # tpyc: mir(covered) mir_summary(known) warning(/never consumed/)
    return t.n


# free function: a container element
def in_list(name: str) -> int32:  # tpyc: mir(uncovered /^container holds a borrow$/)
    xs = [Tok(name, 1)]
    return len(xs)


# free function: an Optional payload
def maybe(t: Tok | None) -> int32:  # tpyc: mir(uncovered /^wrapper holds a borrow$/)
    if t is not None:
        return t.n
    return 0


# free function: a tuple member
def paired(t: Tok) -> int32:  # tpyc: mir(uncovered /^wrapper holds a borrow$/)
    pair = (t, 1)
    return pair[0].n


# free function: a record argument built on one branch is a deferred temporary; its backing stores name's loan
def deferred(name: str, flag: bool) -> int32:  # tpyc: mir(certified)
    return size(Tok(name, 1)) if flag else 0


# free function: a select's conditional record storage stores a's loan where it is engaged
def selected(owner: Tok, a: str, flag: bool) -> int32:  # tpyc: mir(certified)
    saved = owner if flag else Tok(a, 2)
    return saved.n


# free function: an in-place reseat replaces the one object's stored loan; nothing views what it replaces
def branch(k: int32, flag: bool) -> None:  # tpyc: mir(covered)
    a = mk(k)
    t = Tok("lit", 1)
    if flag:
        t = Tok(a, 2)
    print("branch", t.s)


# free function: an inherited constructor reuses the base's stored loan
def child(s: str) -> int32:  # tpyc: mir(covered) mir_summary(known)
    c = Child(s, 1)
    return c.n


def main() -> None:
    alias_replaced(1, False)
    copied_rebind(1, False)
    r = R(mk(1), "abc")
    sibling_view(r, False)
    sibling_copy(r)
    aliased_params(r, r, False)
    temporary_source(2, False)
    static_source(3)
    print("callee_reads", callee_reads(4))
    print("callee_reads_late", callee_reads_late(5, False))
    callee_replaces(6, False)
    b = Buf(mk(7))
    t = Tok(b.buf, 1)
    print("late_read", late_read(t, b, False))
    print("alias_late", alias_late(t, b, False))
    loop_alias(t, b, 2, False)
    print("pick_late", pick_late(t, Tok("q", 2), b, False))
    detached_view(8, False)
    print("first", first(Tok("zzz", 1)))
    print("size", size(t))
    print("literal_member", literal_member())
    s = "rt"
    retarget(t, s)
    print("retarget", t.s)
    print("consume", consume(Tok("r", 3)))
    print("in_list", in_list("w"))
    print("maybe", maybe(t))
    print("paired", paired(t))
    print("deferred", deferred("d", True), deferred("d", False))
    print("selected", selected(t, "e", True), selected(t, "e", False))
    print("child", child("c"))
    branch(9, False)


main()
