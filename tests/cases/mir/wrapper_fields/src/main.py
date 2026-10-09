# MIR pins for Optional record fields (`p: P | None`, a plain loan-free record stored inline).
# Storing a `P | None` into a field copies the payload (warned); CPython aliases -- intended.
# A conflict section replaces the field only on a path the run does not take (`flag` is False).
from tpy import Own, int32, readonly


class P:
    x: int32

    def __init__(self, x: int32) -> None:  # tpyc: mir(covered)
        self.x = x

    def bump(self) -> None:  # tpyc: mir(covered) mir_summary(known)
        self.x += 1


class H:
    p: P | None
    n: int32

    # constructor: an Optional record parameter's payload copied into the field
    def __init__(self, p: P | None, n: int32) -> None:  # tpyc: mir(covered)
        self.p = p  # tpyc: warning(/copies P \| None into field/)
        self.n = n

    # method: the field emptied through self, a published write of param0.p
    def reset(self) -> None:  # tpyc: mir(covered) mir_summary(known)
        self.p = None  # tpyc: mir_write(self.p)

    # method conflict: a holder of the payload, then the payload replaced through self
    def via_self(self, flag: bool) -> int32:  # tpyc: mir(conflict /replacement/)
        o = self.p
        if flag:
            self.p = P(9)  # tpyc: mir_write(self.p) warning(/Mutation of 'self.p' while borrowed/)
        if o is not None:
            return o.x
        return 0


class Empty:
    p: P | None

    # constructor: the field left empty
    def __init__(self) -> None:  # tpyc: mir(covered)
        self.p = None


class Built:
    p: P | None

    # constructor: the payload built by its own constructor
    def __init__(self, x: int32) -> None:  # tpyc: mir(covered)
        self.p = P(x)


class Pair:
    t: tuple[int32, P]

    # kept refusal: a tuple field holding a record
    def __init__(self, t: tuple[int32, P]) -> None:  # tpyc: mir(uncovered /unsupported record fields/)
        self.t = t  # tpyc: warning(/copies P into field \(tuple element 1\)/)


class Ro:
    p: readonly[P | None]

    # constructor: a readonly Optional record field (a store to it is a sema error)
    def __init__(self, p: P | None) -> None:  # tpyc: mir(covered)
        self.p = p  # tpyc: warning(/copies P \| None into field/)


class Num:
    k: int32 | None

    # kept refusal: an Optional of a scalar
    def __init__(self, k: int32 | None) -> None:  # tpyc: mir(uncovered /unsupported record fields/)
        self.k = k


def clear(h: H) -> None:  # tpyc: mir(covered) mir_summary(known)
    h.p = None


# free function: a narrowed read of the payload's field
def read(h: H) -> int32:  # tpyc: mir(covered) mir_summary(known)
    if h.p is not None:
        return h.p.x
    return 0


# free function conflict: `a` and `b` may name one holder; emptying b.p ends what o holds
def alias(a: H, b: H, flag: bool) -> int32:  # tpyc: mir(conflict /replacement/)
    o = a.p
    if flag:
        b.p = None
    if o is not None:
        return o.x
    return 0


# safe sibling: the holder is read before b.p is emptied
def alias_read_first(a: H, b: H) -> int32:  # tpyc: mir(covered)
    o = a.p  # tpyc: mir_borrows(o, a.p[payload])
    r = 0
    if o is not None:
        r = o.x
    b.p = None
    return r


# safe sibling: a write to a different field ends nothing the holder holds
def alias_other_field(a: H, b: H) -> int32:  # tpyc: mir(covered)
    o = a.p  # tpyc: mir_borrows(o, a.p[payload])
    b.n = 1
    if o is not None:
        return o.x
    return 0


# free function conflict: the callee empties the field the holder borrows from
def callee(h: H, flag: bool) -> int32:  # tpyc: mir(conflict /replacement/)
    o = h.p
    if flag:
        clear(h)
    if o is not None:
        return o.x
    return 0


# kept refusal: sema drops the narrowing at the method call, so the read is unproven
def fresh(h: H, flag: bool) -> int32:  # tpyc: mir(uncovered /unproven optional field read/)
    if h.p is not None:
        if flag:
            h.reset()
        return h.p.x  # tpyc: warning(/Potential None access/)
    return 0


# presence kill: b may be a, so emptying b.p ends the proof that a.p is engaged
def fresh_alias(a: H, b: H) -> int32:  # tpyc: mir(uncovered /presence proof/)
    if a.p is not None:
        b.p = None
        return a.p.x
    return 0


# presence kill: the callee may empty b.p, which may be a.p (its published write of param0.p)
def fresh_call(a: H, b: H) -> int32:  # tpyc: mir(uncovered /presence proof/)
    if a.p is not None:
        clear(b)
        return a.p.x
    return 0


def empties(h: H) -> bool:  # tpyc: mir(covered) mir_summary(known)
    h.p = None
    return True


# presence kill: the proof stored on the `and` operand dies at the callee's write of b.p
def fresh_condition(a: H, b: H) -> int32:  # tpyc: mir(uncovered /presence proof/)
    if a.p is not None and empties(b):
        return a.p.x
    return 0


# presence kept: writing a different field leaves a.p engaged
def keep_other_field(a: H, b: H) -> int32:  # tpyc: mir(covered)
    if a.p is not None:
        b.n = 2
        return a.p.x
    return 0


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


# conflict: `o` may point into a.p, which emptying or reassigning a.p ends before o is read
def copy_both(a: H, b: H, o: P | None, flag: bool) -> None:  # tpyc: mir(conflict /replacement/)
    if flag:
        a.p = b.p  # tpyc: warning(/copies P \| None into field/)
    b.p = o  # tpyc: warning(/copies P \| None into field/)


# kept refusal: a field write does not narrow (BUGS.md#optional-field-write-not-narrowing), so the read is checked
def written_then_read(h: H) -> int32:  # tpyc: mir(uncovered /unproven optional field read/)
    h.p = P(2)
    return h.p.x  # tpyc: warning(/Potential None access/)


# kept refusal: a copy of a narrowed payload has no copy node; the read warns (BUGS.md#optional-field-write-not-narrowing)
def copied_then_read(a: H, b: H) -> int32:  # tpyc: mir(uncovered /optional field copy from a narrowed payload/)
    if b.p is not None:
        a.p = b.p  # tpyc: warning(/copies P into field/)
        return a.p.x  # tpyc: warning(/Potential None access/)
    return 0


def find(x: int32) -> Own[P | None]:  # tpyc: mir(uncovered /unsupported return type/)
    return P(x)


# kept refusal: the field takes a call's owned Optional result (moved; the copy warning is
# BUGS.md#own-optional-param-field-store-copies)
def store_found(h: H) -> None:  # tpyc: mir(uncovered /record initializer type mismatch/)
    h.p = find(11)  # tpyc: warning(/copies P \| None into field/)


# the payload lent to a callee that writes it: published as a write of the whole field
def bump_payload(h: H) -> None:  # tpyc: mir(covered) mir_summary(known)
    if h.p is not None:
        h.p.bump()


# kept refusal: a borrow of the narrowed payload has no THIR storage-borrow fact
def payload_borrow(h: H) -> int32:  # tpyc: mir(uncovered /storage borrow fact/)
    if h.p is not None:
        q = h.p
        return q.x
    return 0


# kept refusal: a write under the payload
def payload_write(h: H) -> None:  # tpyc: mir(uncovered /optional field payload write/)
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


# kept refusal: a constructed argument needs THIR's temporary plan, which this body lacks
def local_temporary() -> None:  # tpyc: mir(uncovered /temporary plan/)
    h = H(P(1), 2)
    if h.p is not None:
        print("local_temporary", h.p.x)


def main() -> None:
    a = H(P(1), 10)
    b = H(P(2), 20)
    print("via_self", a.via_self(False))
    print("read", read(a))
    print("alias", alias(a, b, False))
    print("alias_read_first", alias_read_first(a, b))
    print("alias_other_field", alias_other_field(a, b))
    print("callee", callee(a, False))
    print("fresh", fresh(H(P(3), 30), False))
    print("fresh_alias", fresh_alias(H(P(4), 40), H(P(5), 50)))
    print("fresh_call", fresh_call(H(P(6), 60), H(P(7), 70)))
    print("keep_other_field", keep_other_field(a, b))
    print("fresh_condition", fresh_condition(H(P(12), 1), H(P(13), 2)))
    print("refill", refill(b))
    c = H(None, 60)
    copy_field(c, b)
    print("copy_field", read(c))
    seven = P(7)
    copy_param(b, seven)
    print("copy_param", read(b))
    copy_both(c, b, seven, False)
    print("copy_both", read(b))
    bump_payload(c)
    print("bump_payload", read(c))
    print("payload_borrow", payload_borrow(c))
    payload_write(c)
    print("payload_write", read(c))
    d = H(None, 1)
    print("written_then_read", written_then_read(d))
    print("copied_then_read", copied_then_read(d, H(P(14), 1)))
    store_found(d)
    print("store_found", read(d))
    print("ro_read", ro_read(Ro(P(15))))
    local()
    print("local_forms", local_forms(a))
    local_temporary()
    print("built_forms", built_forms())
    e = Empty()
    if e.p is None:
        print("empty", 0)
    u = Built(8)
    if u.p is not None:
        print("built", u.p.x)
    pr = Pair((1, P(1)))
    print("pair", pr.t[1].x)
    m = Num(None)
    if m.k is None:
        print("num", 0)


main()
