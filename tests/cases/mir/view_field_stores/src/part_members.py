# MIR over whole members holding a view: replaced, reseated through a borrowed parameter, built
# by base and member constructors, and an element store under a record's view.
from fixtures import Kid, R, Tok, mk
from part_own import Wrap
from tpy import Own, StrView, copy, int32


class Holder:
    inner: R

    def __init__(self, inner: R) -> None:
        self.inner = copy(inner)

    # method: a whole member replaced through self stores s's loan in the caller's object (a transfer);
    # s viewing the replaced member is a precondition the body publishes, checked at callers MIR analyzes, as member_param's
    def swap(self, s: str) -> None:  # tpyc: mir(conflict /alias_obligation/) mir_summary(known)
        self.inner = R(s, s)


# free function: a whole member holding a view replaced by a record viewing the member it replaces
# (BUGS.md#record-view-member-source-replaced)
def member_self(k: int32, flag: bool) -> None:  # tpyc: mir(conflict /replacement/)
    o = Holder(R(mk(k), "static"))
    if flag:
        o.inner = R(mk(k + 1), o.inner.buf)  # tpyc: warning(/this assignment frees the old value of 'o.inner', and 'o.inner.s' may still view it/)
    print("member_self", o.inner.s)


# free function: the replacing member views static storage, so nothing views the buffer it replaces
def member_fresh(k: int32) -> None:  # tpyc: mir(covered)
    o = Holder(R(mk(k), "static"))
    o.inner = R(mk(k + 1), "lit")
    print("member_fresh", o.inner.s)


# free function: a reference to the member sees the new record, which views the buffer it replaced
# (BUGS.md#record-view-member-source-replaced); sema warns (mutation while borrowed), MIR warns "may"
def member_alias(k: int32, flag: bool) -> None:  # tpyc: mir(conflict /replacement/)
    o = Holder(R(mk(k), "static"))
    a = o.inner
    if flag:
        o.inner = R(mk(k + 1), a.buf)  # tpyc: warning(/while borrowed/) warning(/this assignment may overwrite what 'a' refers to in place/) warning(/this assignment frees the old value of 'o.inner', and 'o.inner.s' may still view it/)
    print("member_alias", a.s)


# free function: a whole member replaced through a borrowed parameter stores s's loan (a transfer).
# The new o.inner.s views s, so s viewing the replaced member (`member_param(o, o.inner.buf)`)
# dangles: a precondition the body publishes, checked at callers MIR analyzes.
def member_param(o: Holder, s: str) -> None:  # tpyc: mir(conflict /alias_obligation/) mir_summary(known)
    o.inner = R(s, s)


# free function: a view member written under a member record of the caller's object
def fill_inner(o: Holder, s: str) -> None:  # tpyc: mir(covered) mir_summary(known)
    o.inner.s = s


# free function: the callee stored s's loan in o.inner; s is replaced while o lives
# (BUGS.md#record-view-member-source-replaced)
def fill_inner_replaced(k: int32, flag: bool) -> None:  # tpyc: mir(conflict /replacement/)
    o = Holder(R("member", "static"))
    s = mk(k)
    fill_inner(o, s)
    if flag:
        s = mk(k + 1)  # tpyc: warning(/this assignment frees the old value of 's', and 'o.inner.s' may still view it/)
    print("fill_inner_replaced", o.inner.s)


class Base:
    s: StrView

    def __init__(self, s: str) -> None:
        self.s = s


class Derived(Base):
    k: int32

    # constructor: the base's view member stores the loan the derived constructor's parameter lends
    def __init__(self, s: str, k: int32) -> None:  # tpyc: mir(covered)
        super().__init__(s)
        self.k = k


class Fixed(Base):
    # constructor: a literal base argument stores static storage
    def __init__(self) -> None:  # tpyc: mir(covered)
        super().__init__("abc")


class Shell:
    inner: Tok

    # constructor: a member built by its own constructor stores the loan the parameter lends
    def __init__(self, s: str) -> None:  # tpyc: mir(covered)
        self.inner = Tok(s, 1)


# free function: a base's stored loan of the argument the caller lends
def derived_lent(s: str) -> int32:  # tpyc: mir(covered) mir_summary(known)
    d = Derived(s, 1)
    return len(d.s) + d.k


# free function: the base's stored loan views a temporary of the declaration
# (BUGS.md#record-view-field-escapes-local-buffer)
def derived_temp(k: int32, flag: bool) -> None:  # tpyc: mir(conflict /scope_end/)
    d = Derived(mk(k), 1)
    if flag:
        print("derived_temp", d.s)
    print("derived_temp", d.k)


# free function: an inherited constructor's stored loan views a temporary of the declaration
# (BUGS.md#record-view-field-escapes-local-buffer)
def kid_temp(k: int32, flag: bool) -> None:  # tpyc: mir(conflict /scope_end/)
    d = Kid(mk(k), 1)
    if flag:
        print("kid_temp", d.s)
    print("kid_temp", d.n)


# free function: a literal base argument's static storage
def fixed_base() -> int32:  # tpyc: mir(covered) mir_summary(known)
    f = Fixed()
    return len(f.s)


# free function: a member constructor's stored loan views a temporary of the declaration
# (BUGS.md#record-view-field-escapes-local-buffer)
def shell_temp(k: int32, flag: bool) -> None:  # tpyc: mir(conflict /scope_end/)
    sh = Shell(mk(k))
    if flag:
        print("shell_temp", sh.inner.s)
    print("shell_temp", sh.inner.n)



# free function: a view member read under a member record of a borrowed parameter (nested key, seeded)
def nested_read(o: Holder) -> StrView:  # tpyc: mir(covered) mir_summary(known)
    return o.inner.s


# free function: a body-local buffer stored by a whole member replacement into the caller's object
# (BUGS.md#record-view-field-escapes-local-buffer)
def member_param_local(o: Holder, k: int32) -> None:  # tpyc: mir(conflict /store_escape/)
    buf = mk(k)
    o.inner = R(buf, buf)  # tpyc: warning(/'buf' is freed when 'member_param_local' returns/)


# free function: the same into an Own parameter's object, which is the caller's temporary
# (BUGS.md#record-view-field-escapes-local-buffer)
# the Own parameter is only read or written in place, never handed on: the warning is right
def member_own_local(o: Own[Holder], k: int32) -> int32:  # tpyc: mir(conflict /store_escape/) warning(/never consumed/)
    buf = mk(k)
    o.inner = R(buf, buf)
    return len(o.inner.s)


# free function: a member replacement through a name that may reach either of two objects; known false
# warning (BUGS.md#mir-may-warning-correlated-conditions): `src` is replaced only when `x` is `a`
def member_either(k: int32, flag: bool) -> None:  # tpyc: mir(conflict /replacement/)
    src = mk(k)
    a = Holder(R("a", src))
    b = Holder(R("b", "static"))
    x = a if flag else b
    x.inner = R("c", "lit")
    if flag:
        src = mk(k + 1)  # tpyc: warning(/this assignment frees the old value of 'src', and 'a.inner.s' may still view it/)
    print("member_either", a.inner.s, b.inner.s)


def make_r(s: str) -> Own[R]:
    return R("made", s)


# free function: a member replaced by a call's owned result stores what the callee's result transfers
# (BUGS.md#record-view-member-source-replaced)
def member_from_call(k: int32, flag: bool) -> None:  # tpyc: mir(conflict /replacement/)
    src = mk(k)
    o = Holder(R("member", "static"))
    o.inner = make_r(src)
    if flag:
        src = mk(k + 1)  # tpyc: warning(/this assignment frees the old value of 'src'/)
    print("member_from_call", o.inner.s)


# free function: a copy of a name reaching either of two records carries both records' loans
# (BUGS.md#record-view-member-source-replaced)
def copy_either(k: int32, flag: bool) -> None:  # tpyc: mir(conflict /replacement/)
    src = mk(k)
    a = Tok(src, 1)
    b = Tok("static", 2)
    t = a if flag else b
    w = Wrap(copy(t))
    if flag:
        src = mk(k + 1)  # tpyc: warning(/this assignment frees the old value of 'src', and 'w.t.s' may still view it/)
    print("copy_either", w.t.s)


class Items:
    items: list[str]
    s: StrView

    def __init__(self, items: list[str], s: StrView) -> None:
        self.items = copy(items)
        self.s = s

    def setv(self, v: StrView) -> None:
        self.s = v


# free function: an element store through the record whose view member views that element; the store
# rebinds the record's holder, but r.s still views the replaced element (BUGS.md#record-view-member-source-replaced)
def element_view_replaced(r: Items, k: int32, flag: bool) -> None:  # tpyc: mir(conflict /replacement/)
    r.setv(r.items[0])
    if flag:
        r.items[0] = mk(k)  # tpyc: mir_borrows(r.s, r.items[elements]|r.s) warning(/this assignment frees an element of 'r.items', and 'r.s' may still view it/)
    print("element_view_replaced", r.s)


def main(flag: bool) -> None:
    member_self(1, False)
    member_fresh(1)
    member_alias(1, False)
    hd = Holder(R("member", "static"))
    member_param(hd, "p")
    print("member_param", hd.inner.s)
    fill_inner(hd, "q")
    print("fill_inner", hd.inner.s)
    print("nested_read", nested_read(hd))
    if flag:
        member_param_local(hd, 1)
    print("member_own_local", member_own_local(Holder(R("own", "static")), 1))
    member_either(1, False)
    member_from_call(1, False)
    copy_either(1, False)
    hd.swap("z")
    print("swap", hd.inner.s)
    fill_inner_replaced(1, False)
    print("derived_lent", derived_lent("dl"))
    derived_temp(1, False)
    kid_temp(1, False)
    print("fixed_base", fixed_base())
    shell_temp(1, False)
    element_view_replaced(Items([mk(1)], "static"), 2, False)
