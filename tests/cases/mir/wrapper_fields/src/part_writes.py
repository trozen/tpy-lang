# Whole writes of an Optional record field: constructed, copied, and the kept refusals.
from tpy import Own, copy, int32
from fixtures import H, P, read


# whole-field writes: a constructed payload, then a fresh narrowed read
def refill(h: H) -> int32:  # tpyc: mir(covered) mir_summary(known)
    h.p = P(2)  # tpyc: mir_write(h.p)
    if h.p is not None:
        return h.p.x
    return 0


# whole-field writes: a copy of another holder's field
def copy_field(a: H, b: H) -> None:  # tpyc: mir(covered) mir_summary(known)
    a.p = b.p  # tpyc: mir_write(a.p) warning(/copies P \| None into field/)


# whole-field writes: a copy of the payload an Optional parameter points at
def copy_param(b: H, o: P | None) -> None:  # tpyc: mir(covered)
    b.p = o  # tpyc: mir_write(b.p) warning(/copies P \| None into field/)


# conflict: `o` may point into a.p, which reassigning a.p ends before o is read (a no-alias obligation)
def copy_both(a: H, b: H, o: P | None, flag: bool) -> None:  # tpyc: mir(conflict /alias_obligation/)
    if flag:
        a.p = b.p  # tpyc: warning(/copies P \| None into field/)
    b.p = o  # tpyc: warning(/copies P \| None into field/)


# kept refusal: a field write does not narrow (BUGS.md#optional-field-write-not-narrowing), so the read is checked
def written_then_read(h: H) -> int32:  # tpyc: mir(uncovered /unproven optional field read/)
    h.p = P(2)
    return h.p.x  # tpyc: warning(/Potential None access/)


# kept refusal: the narrowed member is copied as its whole Optional, a field form MIR does not take; the read warns (BUGS.md#optional-field-write-not-narrowing)
def copied_then_read(a: H, b: H) -> int32:  # tpyc: mir(uncovered /unsupported field type or form/)
    if b.p is not None:
        a.p = b.p  # tpyc: warning(/copies P into field/)
        return a.p.x  # tpyc: warning(/Potential None access/)
    return 0


def find(x: int32) -> Own[P | None]:  # tpyc: mir(uncovered /unsupported return type/)
    return P(x)


# kept refusal: the field takes a call's owned Optional result (moved, silently)
def store_found(h: H) -> None:  # tpyc: mir(uncovered /record initializer type mismatch/)
    h.p = find(11)  # tpyc: ok


class Into:
    up: P | int32
    o: P | None
    q: P
    t: tuple[P, int32]

    def __init__(self) -> None:  # tpyc: mir(covered)
        self.up = 1
        self.o = None
        self.q = P(0)
        self.t = (P(0), 0)


# a narrowed payload copied into each target: a record copy out of the payload place (the bare
# forms copy, warned, where CPython aliases; intended)
def payload_into_union(a: H, b: Into) -> None:  # tpyc: mir(covered) mir_summary(known)
    if a.p is not None:
        b.up = a.p  # tpyc: mir_write(b.up) warning(/copies P into field/)


# a read of a.p after b.q is replaced carries the plain-field source's no-alias obligation
def payload_into_plain(a: H, b: Into) -> None:  # tpyc: mir(conflict /alias_obligation/) mir_summary(known)
    if a.p is not None:
        b.q = a.p  # tpyc: mir_copy(b.q) warning(/copies P into field/)
        b.q = copy(a.p)  # tpyc: mir_copy(b.q)


def payload_into_wrappers(a: H, b: Into) -> None:  # tpyc: mir(conflict /alias_obligation/) mir_summary(known)
    if a.p is not None:
        b.o = copy(a.p)  # tpyc: mir_write(b.o)
        b.t = (copy(a.p), 1)  # tpyc: mir_write(b.t)


def payload_into_local(a: H) -> int32:  # tpyc: mir(covered) mir_summary(known)
    if a.p is not None:
        r = copy(a.p)
        a.p.x = 40
        return r.x
    return 0


def payload_into_element(a: H, xs: list[P]) -> None:  # tpyc: mir(covered) mir_summary(known)
    if a.p is not None:
        xs[0] = copy(a.p)


# kept refusals: copy() into a union field and a bare payload as a tuple member reach MIR as a
# template call
def payload_into_union_copy(a: H, b: Into) -> None:  # tpyc: mir(uncovered /unsupported metadata: cpp_template/)
    if a.p is not None:
        b.up = copy(a.p)


def payload_into_tuple(a: H, b: Into) -> None:  # tpyc: mir(uncovered /unsupported metadata: cpp_template/)
    if a.p is not None:
        b.t = (a.p, 1)  # tpyc: warning(/copies P into field/)


def payload_copies() -> None:
    a = H(P(30), 1)
    b = Into()
    # the bare forms first: each copy() form below replaces what a bare form stored
    payload_into_union(a, b)
    payload_into_tuple(a, b)
    payload_into_plain(a, b)
    payload_into_wrappers(a, b)
    payload_into_union_copy(a, b)
    xs = [P(0)]
    payload_into_element(a, xs)
    print("payload_into local", payload_into_local(a))
    # every target holds its own copy: the source's later write reaches none of them
    print("payload_into", b.q.x, b.t[0].x, xs[0].x, read(a))
    if b.o is not None:
        print("payload_into o", b.o.x)


def main() -> None:
    b = H(P(2), 20)
    print("refill", refill(b))
    c = H(None, 60)
    copy_field(c, b)
    print("copy_field", read(c))
    seven = P(7)
    copy_param(b, seven)
    print("copy_param", read(b))
    copy_both(c, b, seven, False)
    print("copy_both", read(b))
    d = H(None, 1)
    print("written_then_read", written_then_read(d))
    print("copied_then_read", copied_then_read(d, H(P(14), 1)))
    store_found(d)
    print("store_found", read(d))
    payload_copies()
