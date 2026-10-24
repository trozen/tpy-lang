# MIR call summaries of union and tuple parameters: each pointer member lends one object, and a caller passing
# a union or tuple argument lowers. Witness sections (alias_*) are never run: each reads storage the callee replaced.
from tpy import copy, int32, readonly


class P:
    def __init__(self, v: int32) -> None:
        self.v = v

    # method caller: the receiver passed straight to a union parameter, lent in place
    def bump(self) -> None:  # tpyc: mir(covered)
        write_uni(self)


class Q:
    def __init__(self, v: int32) -> None:
        self.v = v


class Cell:
    def __init__(self, v: int32) -> None:
        self.v = v


class H:
    def __init__(self, q: Q) -> None:
        # The copy is intended: each witness replaces h.q while an argument still borrows the stored member.
        self.q = q  # tpyc: warning(/copies Q into field/)


# union record parameter: a write through the payload the narrowing selects
def write_uni(u: P | Q) -> None:  # tpyc: mir(covered) mir_summary(known)
    if isinstance(u, P):
        u.v = 5


# union record parameter: a read of either payload
def read_uni(u: P | Q) -> int32:  # tpyc: mir(covered) mir_summary(known)
    if isinstance(u, P):
        return u.v
    return u.v + 100


# scalar union parameter
def scalar_uni(k: int32 | bool) -> int32:  # tpyc: mir(covered) mir_summary(known)
    if isinstance(k, bool):
        return 1 if k else 0
    return k


# tuple of scalars
def tup_scalar(t: tuple[int32, int32]) -> int32:  # tpyc: mir(covered) mir_summary(known)
    return t[0] + t[1]


# tuple with a record member: a write through the member
def write_tup(t: tuple[P, int32]) -> None:  # tpyc: mir(covered) mir_summary(known)
    t[0].v = t[0].v + t[1]


# mixed-access tuple: one member read, the other written
def mixed(t: tuple[readonly[Cell], Cell]) -> None:  # tpyc: mir(covered) mir_summary(known)
    t[1].v = t[0].v + 1


# caller: a union name and a member-typed name lift into the union parameter
def pass_uni(u: P | Q, p: P) -> int32:  # tpyc: mir(covered)
    write_uni(u)  # the union name under test
    write_uni(p)  # the member-typed lift under test
    return read_uni(u) + read_uni(p)


# caller: tuple literals lent in place, a record name in each record member
def pass_tup(p: P, c: Cell, d: Cell) -> int32:  # tpyc: mir(covered)
    write_tup((p, 1))  # the tuple literal under test
    mixed((c, d))
    return tup_scalar((p.v, 2))


# witness callees: each replaces h.q, then still uses its other parameter
def scal(h: H, q: Q) -> int32:  # tpyc: mir(conflict /alias_obligation/)
    h.q = Q(5)
    return q.v


def tup(h: H, t: tuple[Q, int32]) -> int32:  # tpyc: mir(conflict /alias_obligation/)
    h.q = Q(5)
    return t[0].v


def uni(h: H, u: Q | P) -> int32:  # tpyc: mir(conflict /alias_obligation/)
    h.q = Q(5)
    if isinstance(u, Q):
        return u.v
    return 0


# witness callee: replaces a field of the record its union parameter's member points at, then reads q
def wuni(u: H | P, q: Q) -> int32:  # tpyc: mir(conflict /alias_obligation/)
    if isinstance(u, H):
        u.q = Q(5)
    return q.v


# aliasing caller, scalar twin
def alias_scal(h: H) -> int32:  # tpyc: mir(conflict /argument_alias/)
    return scal(h, h.q)  # tpyc: warning(/^argument 'h\.q' is what 'scal' modifies through parameter 'h' while it still uses 'q'; pass a copy \('copy\(h\.q\)'\) or restructure so the call does not hand over both$/)


# aliasing caller: a tuple literal's member
def alias_tup(h: H) -> int32:  # tpyc: mir(conflict /argument_alias/)
    return tup(h, (h.q, 1))  # tpyc: warning(/^argument 'h\.q' is what 'tup' modifies through parameter 'h' while it still uses 't'; pass a copy bound to a local \('v = copy\(h\.q\)'\) or restructure so the call does not hand over both$/)


# aliasing caller: a named tuple's member
def alias_named_tup(h: H) -> int32:  # tpyc: mir(conflict /argument_alias/)
    t = (h.q, 1)
    return tup(h, t)  # tpyc: warning(/^argument 't\[0\]' is what 'tup' modifies through parameter 'h' while it still uses 't'; pass a copy bound to a local \('v = copy\(t\[0\]\)', then '\(v, t\[1\]\)'\) or restructure so the call does not hand over both$/)


# aliasing caller: a record local lifted into the union parameter
def alias_uni(h: H) -> int32:  # tpyc: mir(conflict /argument_alias/)
    q = h.q
    return uni(h, q)  # tpyc: warning(/^argument 'q' is what 'uni' modifies through parameter 'h' while it still uses 'u'; pass a copy bound to a local \('v = copy\(q\)'\) or restructure so the call does not hand over both$/)


# aliasing caller: the written place reached through the union member's selector
def alias_written_member(h: H) -> int32:  # tpyc: mir(conflict /argument_alias/)
    return wuni(h, h.q)  # tpyc: warning(/^argument 'h\.q' is what 'wuni' modifies through parameter 'u' while it still uses 'q'; pass a copy \('copy\(h\.q\)'\) or restructure so the call does not hand over both$/)


# witness callee: two members of ONE tuple parameter; the write through t[0] replaces what t[1] may be
def two(t: tuple[H, Q]) -> int32:  # tpyc: mir(conflict /alias_obligation/)
    x = t[0]
    x.q = Q(5)
    return t[1].v


# aliasing caller: one tuple literal hands over h and h.q
def alias_two(h: H) -> int32:  # tpyc: mir(conflict /argument_alias/)
    return two((h, h.q))  # tpyc: warning(/^argument 'h\.q' is what 'two' modifies through parameter 't\[0\]' while it still uses 't\[1\]'; pass a copy bound to a local \('v = copy\(h\.q\)'\) or restructure so the call does not hand over both$/)


# witness callees: a holder of a's member's field, replaced through b's
def heldscalar(a: H, b: H) -> int32:  # tpyc: mir(conflict /alias_obligation/)
    q = a.q
    b.q = Q(5)
    return q.v


def heldunion(a: H | P, b: H | P) -> int32:  # tpyc: mir(conflict /alias_obligation/)
    if isinstance(a, H):
        q = a.q
        if isinstance(b, H):
            b.q = Q(5)
        return q.v
    return 0


def heldtuple(a: tuple[H, int32], b: tuple[H, int32]) -> int32:  # tpyc: mir(conflict /alias_obligation/)
    ah = a[0]
    bh = b[0]
    q = ah.q
    bh.q = Q(5)
    return q.v


# aliasing caller, scalar twin of the two below
def same_scalar(h: H) -> int32:  # tpyc: mir(conflict /argument_alias/)
    return heldscalar(h, h)  # tpyc: warning(/^argument 'h' contains 'h\.q', which 'heldscalar' modifies through parameter 'b' while it still uses 'a'; pass a copy \('copy\(h\)'\) or restructure so the call does not hand over both$/)


# aliasing caller: a union-typed name passed twice; the written place is spelled through the member
def same_union(u: H | P) -> int32:  # tpyc: mir(conflict /argument_alias/)
    return heldunion(u, u)  # tpyc: warning(/^argument 'u' contains 'u\.q', which 'heldunion' modifies through parameter 'b' while it still uses 'a'; pass a copy bound to a local \('v = copy\(u\)'\) or restructure so the call does not hand over both$/)


# aliasing caller: a named tuple passed twice; the written place is spelled through the member
def same_tuple(t: tuple[H, int32]) -> int32:  # tpyc: mir(conflict /argument_alias/)
    return heldtuple(t, t)  # tpyc: warning(/^argument 't\[0\]' contains 't\[0\]\.q', which 'heldtuple' modifies through parameter 'b' while it still uses 'a'; pass a copy bound to a local \('v = copy\(t\[0\]\)', then '\(v, t\[1\]\)'\) or restructure so the call does not hand over both$/)


# non-aliasing twins: the other argument is storage the callee does not replace
def apart_scal(h: H, q: Q) -> int32:
    return scal(h, q)  # tpyc: ok


def apart_tup(h: H, q: Q) -> int32:
    return tup(h, (q, 1))  # tpyc: ok


def apart_named_tup(h: H, q: Q) -> int32:
    t = (q, 1)
    return tup(h, t)  # tpyc: ok


def apart_uni(h: H, q: Q) -> int32:
    return uni(h, q)  # tpyc: ok


def apart_written_member(h: H, q: Q) -> int32:
    return wuni(h, q)  # tpyc: ok


# lowered twins of two parameters each: silent here, the callee's obligation becomes the caller's own
# (checked at its callers, as for any two parameters)
def apart_two(h: H, q: Q) -> int32:  # tpyc: mir(conflict /alias_obligation/)
    return two((h, q))  # tpyc: ok


# the same call on the body's own distinct objects: the call checks the obligation and passes
def apart_two_local() -> int32:  # tpyc: mir(covered)
    h = H(Q(1))
    q = Q(2)
    return two((h, q))  # tpyc: ok


def apart_scalar_twin(a: H, b: H) -> int32:  # tpyc: mir(conflict /alias_obligation/)
    return heldscalar(a, b)  # tpyc: ok


def apart_union(u: H | P, w: H | P) -> int32:  # tpyc: mir(conflict /alias_obligation/)
    return heldunion(u, w)  # tpyc: ok


def apart_tuple_twin(t: tuple[H, int32], w: tuple[H, int32]) -> int32:  # tpyc: mir(conflict /alias_obligation/)
    return heldtuple(t, w)  # tpyc: ok


# the remedy alias_uni's warning names, applied: the callee reads a copy h.q's replacement leaves alone
def fixed_uni(h: H) -> int32:  # tpyc: mir(covered)
    q = h.q
    v = copy(q)
    return uni(h, v)  # tpyc: ok


# the remedy same_union's warning names, applied: the union's copy bound first
def fixed_union(u: H | P) -> int32:
    v = copy(u)
    return heldunion(u, v)  # tpyc: ok


# the remedy same_tuple's warning names, applied: the member's copy bound first, the named tuple rebuilt around it
def fixed_tuple(t: tuple[H, int32]) -> int32:
    v = copy(t[0])
    return heldtuple(t, (v, t[1]))  # tpyc: ok


# witness callee: a one-element tuple parameter
def tup1(h: H, t: tuple[Q]) -> int32:  # tpyc: mir(conflict /alias_obligation/)
    h.q = Q(5)
    return t[0].v


# aliasing caller: a named one-element tuple; the rebuilt tuple keeps its trailing comma
def alias_one(h: H) -> int32:  # tpyc: mir(conflict /argument_alias/)
    t = (h.q,)
    return tup1(h, t)  # tpyc: warning(/^argument 't\[0\]' is what 'tup1' modifies through parameter 'h' while it still uses 't'; pass a copy bound to a local \('v = copy\(t\[0\]\)', then '\(v,\)'\) or restructure so the call does not hand over both$/)


def main() -> None:
    p = P(1)
    print("pass_uni", pass_uni(p, P(2)), p.v)
    q = Q(3)
    print("pass_uni_q", pass_uni(q, P(4)), q.v)
    # mixed writes d.v = c.v + 1: a copy of d would leave 0
    d = Cell(0)
    print("pass_tup", pass_tup(p, Cell(9), d), p.v, d.v)
    p.bump()
    print("bump", p.v)
    print("scalar_uni", scalar_uni(3), scalar_uni(True))
    print("apart_scal", apart_scal(H(Q(7)), Q(8)))
    print("apart_tup", apart_tup(H(Q(7)), Q(9)))
    print("apart_named_tup", apart_named_tup(H(Q(7)), Q(10)))
    print("apart_uni", apart_uni(H(Q(7)), Q(11)))
    print("apart_written_member", apart_written_member(H(Q(7)), Q(13)))
    print("apart_two", apart_two(H(Q(7)), Q(16)))
    print("apart_two_local", apart_two_local())
    print("apart_scalar_twin", apart_scalar_twin(H(Q(17)), H(Q(18))))
    print("apart_union", apart_union(H(Q(14)), H(Q(15))))
    print("apart_tuple_twin", apart_tuple_twin((H(Q(19)), 1), (H(Q(20)), 2)))
    print("fixed_uni", fixed_uni(H(Q(21))))
    print("fixed_union", fixed_union(H(Q(22))))
    print("fixed_tuple", fixed_tuple((H(Q(23)), 1)))


main()
