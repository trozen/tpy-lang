# MIR over owned results whose members hold a loan: what the result keeps, and the source
# replaced while it lives.
from fixtures import Buf, Outer, Tok, mk
from tpy import Own, int32


# free function: an owned result whose member holds the parameter's loan
def make(s: str) -> Own[Tok]:  # tpyc: mir(covered) mir_summary(known)
    return Tok(s, 2)


# free function: the result of make holds b's loan; b is replaced while t lives
# (BUGS.md#record-view-member-source-replaced)
def made_replaced(k: int32, flag: bool) -> None:  # tpyc: mir(conflict /replacement/)
    b = mk(k)
    t = make(b)  # tpyc: mir_borrows(t.s, b)
    if flag:
        b = mk(k + 1)  # tpyc: warning(/this assignment frees the old value of 'b'/)
    print("made_replaced", t.s)


# free function: an owned result whose member views a body-local buffer
# (BUGS.md#record-view-field-escapes-local-buffer)
def make_local(k: int32) -> Own[Tok]:  # tpyc: mir(conflict /store_escape/)
    buf = mk(k)
    return Tok(buf, 3)  # tpyc: warning(/'buf' is freed when 'make_local' returns/)


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
        b.text = mk(k + 1)  # tpyc: warning(/this assignment frees the old value of 'b.text', and 't.s' may still view it/)
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
        b.text = mk(k + 1)  # tpyc: warning(/this assignment frees the old value of 'b.text', and 'o.inner.s' may still view it/)
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
        b.text = mk(k + 1)  # tpyc: warning(/this assignment frees the old value of 'b.text'/)
    print("ident_kept", t.s)  # tpyc: mir_borrows(t.s, b.text)


# free function: an owned result built in the body views s, which may view b.text, replaced before the
# return: a precondition the body publishes, checked at callers MIR analyzes (BUGS.md#record-view-member-source-replaced)
def make_replace(s: str, b: Buf, flag: bool) -> Own[Tok]:  # tpyc: mir(conflict /alias_obligation/)
    t = Tok(s, 1)
    if flag:
        b.text = mk(4)
    return t


# free function: the call replaces b.text and fills t with its loan, so passing b.text beside b is
# warned at the call: make_replace's no-alias obligation (BUGS.md#record-view-member-source-replaced)
def made_replace(k: int32, flag: bool) -> None:  # tpyc: mir(conflict /replacement/)
    b = Buf(mk(k))
    t = make_replace(b.text, b, flag)  # tpyc: warning(/argument 'b.text' is what 'make_replace' modifies/) warning(/this call frees the old value of 'b.text'/)
    print("made_replace", t.s)


def main(flag: bool) -> None:
    print("make", make("m").s)
    made_replaced(1, False)
    if flag:
        print("make_local", make_local(1).n)
    own_result_seed(1, False)
    own_member_seed(1, False)
    ident_kept(1, False)
    made_replace(1, False)
