# The narrowed payload of an Optional record field: lent, returned, written under, and local holders.
from tpy import StrView, int32
from fixtures import Built, Empty, H, P, Ro, S, SH, read


# the payload lent to a callee that writes it: published at the payload's own path
def bump_payload(h: H) -> None:  # tpyc: mir(covered) mir_summary(known)
    if h.p is not None:
        h.p.bump()


# the narrowed payload bound alone: a holder of the place h.p[payload]
def payload_borrow(h: H) -> int32:  # tpyc: mir(covered)
    if h.p is not None:
        q = h.p
        return q.x
    return 0


# a return of an Optional field's payload is published at the payload's path,
# as a borrowed record parameter's field is
def payload_return(h: H, fallback: P) -> P:  # tpyc: mir(covered) mir_summary(known)
    if h.p is not None:
        return h.p
    return fallback


def id_p(p: P) -> P:  # tpyc: mir(covered) mir_summary(known)
    return p


# the payload returned through a call that lends it back
def payload_return_call(h: H, fallback: P) -> P:  # tpyc: mir(covered) mir_summary(known)
    if h.p is not None:
        return id_p(h.p)
    return fallback


def set_s(o: S | None, v: str) -> None:  # tpyc: mir(covered) mir_summary(known)
    if o is not None:
        o.s = v


# presence kept: a write strictly inside the payload keeps h.o's selection
def keep_payload(h: SH, flag: bool) -> int32:  # tpyc: mir(covered)
    if flag:
        h.o = None
    o = h.o
    if h.o is not None:
        set_s(o, "z")
        return len(h.o.s)
    return 0


# a field under the payload is returned (a view of its str)
def payload_sub_return(h: SH, fallback: S) -> StrView:  # tpyc: mir(covered) mir_summary(known)
    if h.o is not None:
        return h.o.s
    return fallback.s


def set_field(t: S, value: str) -> int32:  # tpyc: mir(conflict /alias_obligation/)
    t.s = "changed"
    return len(value)


# witness, never run: a body returning the payload keeps its unrelated warning
def payload_return_warning(h: H, fallback: P, t: S) -> P:  # tpyc: mir(conflict /argument_alias/) mir_summary(known)
    set_field(t, t.s)  # tpyc: warning(/argument 't.s' is what 'set_field' modifies through parameter 't' while it still uses 'value'/)
    if h.p is not None:
        return h.p
    return fallback


# a write under the payload: a field of the payload's place
def payload_write(h: H) -> None:  # tpyc: mir(covered) mir_summary(known)
    if h.p is not None:
        h.p.x = 5


# readonly field: a narrowed read through a readonly Optional record field
def ro_read(r: Ro) -> int32:  # tpyc: mir(covered) mir_summary(known)
    if r.p is not None:
        return r.p.x
    return 0


# local holder: built from a named record, then a narrowed read
def local() -> None:  # tpyc: mir(covered)
    q = P(1)
    h = H(q, 2)
    if h.p is not None:
        print("local", h.p.x)


# local holder from an empty argument and from another holder's field
def local_forms(a: H) -> int32:  # tpyc: mir(covered)
    e = H(None, 3)
    f = H(a.p, 4)
    if f.p is not None:
        return f.p.x + e.n
    return e.n


# callers constructing an empty field and a payload built by its own constructor
def built_forms() -> int32:  # tpyc: mir(covered)
    e = Empty()
    u = Built(8)
    if u.p is not None and e.p is None:
        return u.p.x
    return 0


# a constructed argument is a temporary of the constructor call's own expression
def local_temporary() -> None:  # tpyc: mir(covered)
    h = H(P(1), 2)
    if h.p is not None:
        print("local_temporary", h.p.x)


def main() -> None:
    a = H(P(1), 10)
    c = H(P(2), 60)
    seven = P(7)
    bump_payload(c)
    print("bump_payload", read(c))
    print("payload_borrow", payload_borrow(c))
    print("payload_return", payload_return(c, seven).x)
    print("payload_return_call", payload_return_call(c, seven).x)
    print("payload_sub_return", payload_sub_return(SH("sixteen"), S("none")))
    print("keep_payload", keep_payload(SH("seventeen"), False))
    payload_write(c)
    print("payload_write", read(c))
    print("ro_read", ro_read(Ro(P(15))))
    local()
    print("local_forms", local_forms(a))
    local_temporary()
    print("built_forms", built_forms())
