# MIR's lifetime warnings for a replaced record: what the holder sees (freed storage or the new
# value in place), a read after a call that always raises, and leak shapes that stay silent.
from fixtures import P, Tok, fail, mk
from tpy import StrView, int32


class Named:
    x: int32
    n: str

    def __init__(self, x: int32, n: str) -> None:
        self.x = x
        self.n = n


class NamedLine:
    a: Named

    def __init__(self, a: Named) -> None:
        self.a = a


class Points:
    items: list[P]
    inner: P

    def __init__(self) -> None:
        self.items = [P(1)]
        self.inner = P(1)


class Frame:
    pts: Points

    def __init__(self) -> None:
        self.pts = Points()


# replacement of a record, wording: a view of the record's str field sees freed storage (never run)
def record_view(ln: NamedLine, flag: bool) -> bool:
    v: StrView = ln.a.n
    if flag:
        ln.a = Named(9, mk("n"))  # tpyc: warning(/this assignment frees the old value of 'ln.a', but 'v' still views it/)
    return v == "old"


# replacement of a record, wording: a reference into the record's list field (never run)
def record_list(f: Frame, flag: bool) -> int32:
    p = f.pts.items[0]
    if flag:
        f.pts = Points()  # tpyc: warning(/this assignment frees the old value of 'f.pts', but 'p' still refers to it/)
    return p.x


# replacement of a record, wording: a reference to its list field object sees it rewritten in place
def record_list_object(f: Frame, flag: bool) -> int32:
    a = f.pts
    p = a.items
    if flag:
        f.pts = Points()  # tpyc: warning(/this assignment overwrites 'f.pts' in place, but 'p' still refers to it and sees the new value/)
    return p[0].x


# replacement of a record, wording: a reference to a record field of it sees it rewritten in place
def record_field(f: Frame, flag: bool) -> int32:
    a = f.pts
    p = a.inner
    if flag:
        f.pts = Points()  # tpyc: warning(/this assignment overwrites 'f.pts' in place, but 'p' still refers to it and sees the new value/)
    return p.x


def items_of(f: Frame) -> list[P]:
    return f.pts.items


# replacement of a record, wording: the list object a call lends out of it is rewritten in place
def lent_list_object(f: Frame, flag: bool) -> int32:
    lst = items_of(f)
    if flag:
        f.pts = Points()  # tpyc: warning(/this assignment overwrites 'f.pts' in place, but 'lst' still refers to it and sees the new value/)
    return lst[0].x


# replacement of a record, wording: a reference to the record itself sees it rewritten in place
def record_alias(ln: NamedLine, flag: bool) -> int32:
    p = ln.a
    if flag:
        ln.a = Named(7, "c")  # tpyc: warning(/this assignment overwrites 'ln.a' in place, but 'p' still refers to it and sees the new value/)
    return p.x


# known false warning: BUGS.md#mir-warning-after-always-raising-call -- fail() raises on every path,
# so the read after it never runs; the witness does not yet cut a path at a call that always raises
def after_raise() -> None:
    buf = mk("a")
    t = Tok(buf, 1)
    buf = mk("b")  # tpyc: warning(/this assignment frees the old value of 'buf', but 't.s' still views it/)
    fail()
    print("after_raise", t.s[0])


# replacement of a field, attribution: the view is copied into `saved.s`, then rebound onto another
# field, which is freed and read first; the later read of `saved.s` still reads the first field's freed
# value, so the first replacement warns too; its text names `v`, not the copy
# (BUGS.md#mir-copy-holder-warning-names-source) (never run)
def attribution_fields(a: Named, b: Named) -> None:
    saved = Tok("safe", 0)
    v: StrView = a.n
    a.n = mk("A")  # tpyc: warning(/this assignment frees the old value of 'a.n', and 'v' may still view it and is read on line 115/)
    saved.s = v
    v = b.n
    b.n = mk("B")  # tpyc: warning(/this assignment frees the old value of 'b.n', but 'v' still views it and is read on line 114/)
    print("attribution_fields", v[0])
    print("attribution_fields", saved.s[0])


# the same with the two reads swapped (never run)
def attribution_fields_swapped(a: Named, b: Named) -> None:
    saved = Tok("safe", 0)
    v: StrView = a.n
    a.n = mk("A")  # tpyc: warning(/this assignment frees the old value of 'a.n', and 'v' may still view it and is read on line 126/)
    saved.s = v
    v = b.n
    b.n = mk("B")  # tpyc: warning(/this assignment frees the old value of 'b.n', but 'v' still views it and is read on line 127/)
    print("attribution_fields_swapped", saved.s[0])
    print("attribution_fields_swapped", v[0])


# the same with the copy's read behind a branch: the walk goes on past the read of `v` into the next
# block (never run)
def attribution_branch(a: Named, b: Named, flag: bool) -> None:
    saved = Tok("safe", 0)
    v: StrView = a.n
    a.n = mk("A")  # tpyc: warning(/this assignment frees the old value of 'a.n', and 'v' may still view it and is read on line 141/)
    saved.s = v
    v = b.n
    b.n = mk("B")  # tpyc: warning(/this assignment frees the old value of 'b.n', but 'v' still views it and is read on line 139/)
    print("attribution_branch", v[0])
    if flag:
        print("attribution_branch", saved.s[0])


# the loop twin: the copy is read at the top of the next pass, after `v` was rebound and freed in this
# one (never run)
def attribution_loop(a: Named, b: Named, k: int32) -> None:
    saved = Tok("safe", 0)
    v: StrView = "safe"
    for i in range(k):
        print("attribution_loop", saved.s[0])
        v = a.n
        a.n = mk("A")  # tpyc: warning(/this assignment frees the old value of 'a.n', and 'v' may still view it and is read on line 150/)
        saved.s = v
        v = b.n
        b.n = mk("B")  # tpyc: warning(/this assignment frees the old value of 'b.n', but 'v' still views it and is read on line 156/)
        print("attribution_loop", v[0])


# leak shape: the view member is rebound before any read
def overwritten(flag: bool) -> None:
    buf = mk("a")
    t = Tok(buf, 1)
    buf = mk("b")  # tpyc: ok
    t.s = "safe"
    print("overwritten", t.s, buf[0])


# leak shape: the member is restored to the caller's loan before the return
def restore(t: Tok) -> None:
    old: StrView = t.s
    tmp = mk("q")
    t.s = tmp  # tpyc: ok
    t.s = old


def main() -> None:
    try:
        after_raise()
    except IndexError:
        print("after_raise raised")
    print("record_alias", record_alias(NamedLine(Named(1, "a")), False))
    fr = Frame()
    print("record_objects", record_list_object(fr, False), record_field(fr, False), lent_list_object(fr, False))
