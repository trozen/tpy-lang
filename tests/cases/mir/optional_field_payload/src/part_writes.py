# Writes reaching a held payload. The warned bodies read a freed or overwritten payload
# (C++ differs from CPython, which keeps the old object): pinned, never run.
from tpy import StrView, copy, int32
from fixtures import H, P, W, clear, empty_above, refill


# the field emptied by a callee: the payload is freed
def w_call(h: H) -> int32:  # tpyc: mir(conflict /replacement/)
    if h.o is not None:
        r = h.o
        clear(h)  # tpyc: warning(/this call frees the old value of 'h.o', but 'r' still refers to it/)
        return r.v
    return 0


# the field refilled by a callee: the payload is overwritten in place
def w_call_refill(h: H) -> int32:  # tpyc: mir(conflict /replacement/)
    if h.o is not None:
        r = h.o
        refill(h)  # tpyc: warning(/this call overwrites 'h.o' in place, but 'r' still refers to it and sees the new value/)
        return r.v
    return 0


# the field emptied directly while a reseated holder refers to its payload
def reseat_direct(h: H) -> int32:  # tpyc: mir(conflict /replacement/)
    y = h.p
    if h.o is not None:
        y = h.o
        h.o = None  # tpyc: warning(/this assignment frees the old value of 'h.o', but 'y' still refers to it/) warning(/Mutation of 'h.o' while borrowed/)
    return y.v


# the field refilled directly: overwritten in place
def refill_direct(h: H) -> int32:  # tpyc: mir(conflict /replacement/)
    if h.o is not None:
        r = h.o
        h.o = P(2)  # tpyc: warning(/this assignment overwrites 'h.o' in place, but 'r' still refers to it/) warning(/Mutation of 'h.o' while borrowed/)
        return r.v
    return 0


# the field copied from another Optional: freed or overwritten, as the source holds
def copy_in(h: H, src: H) -> int32:  # tpyc: mir(conflict /replacement/)
    if h.o is not None:
        r = h.o
        h.o = src.o  # tpyc: warning(/may free the old value of 'h.o' or overwrite it in place/) warning(/Mutation of 'h.o' while borrowed/) warning(/copies P \| None into field/)
        return r.v
    return 0


# the field copied from a field refilled just before: the source's selection is known, so the
# payload is overwritten in place
def copy_known(h: H, src: H) -> int32:  # tpyc: mir(conflict /replacement/)
    if h.o is not None:
        r = h.o
        src.o = P(5)
        h.o = src.o  # tpyc: warning(/this assignment overwrites 'h.o' in place, but 'r' still refers to it/) warning(/Mutation of 'h.o' while borrowed/) warning(/copies P \| None into field/)
        return r.v
    return 0


# the same through a second copy: the copy reads its source's selection before its own write lands
def copy_known_twice(h: H, h2: H, src: H) -> int32:  # tpyc: mir(conflict /replacement/)
    if h.o is not None:
        r = h.o
        src.o = P(5)
        h2.o = src.o  # tpyc: warning(/copies P \| None into field/)
        h.o = h2.o  # tpyc: warning(/this assignment overwrites 'h.o' in place, but 'r' still refers to it/) warning(/Mutation of 'h.o' while borrowed/) warning(/copies P \| None into field/)
        return r.v
    return 0


# the field copied onto itself while 'r' may keep its payload: the copy reads its own selection
# before its write lands, so the second copy still overwrites in place (never "may free").
# The P(5) warning is right: P(5) overwrites the payload 'r' may hold, which CPython rebinds.
# The self-copy warnings are known false warnings (BUGS.md#opt-field-self-copy-false-warnings):
# assigning the optional to itself changes nothing, as in CPython.
def self_copy(h: H) -> int32:  # tpyc: mir(conflict /replacement/)
    r = P(0)
    if h.o is not None:
        r = h.o
    h.o = P(5)  # tpyc: warning(/this assignment may overwrite what 'r' refers to in place/) warning(/Mutation of 'h.o' while borrowed/)
    h.o = h.o  # tpyc: warning(/this assignment may overwrite what 'r' refers to in place/) warning(/Mutation of 'h.o' while borrowed/) warning(/copies P \| None into field/)
    h.o = h.o  # tpyc: warning(/this assignment may overwrite what 'r' refers to in place/) warning(/Mutation of 'h.o' while borrowed/) warning(/copies P \| None into field/)
    return r.v


# the field copied onto itself with no known prior selection while 'r' holds its payload:
# known false warnings (BUGS.md#opt-field-self-copy-false-warnings), the copy changes nothing
def self_copy_only(h: H) -> int32:  # tpyc: mir(conflict /replacement/)
    r = P(0)
    if h.o is not None:
        r = h.o
    h.o = h.o  # tpyc: warning(/this assignment may free the old value of 'h.o' or overwrite it in place, and 'r' may still refer to it/) warning(/Mutation of 'h.o' while borrowed/) warning(/copies P \| None into field/)
    return r.v


# the record holding the sum replaced: the warning names the sum
def above_alias(w: W) -> int32:  # tpyc: mir(conflict /replacement/)
    h = w.h
    if h.o is not None:
        r = h.o
        w.h = H(0)  # tpyc: warning(/may free the old value of 'w.h.o' or overwrite it in place/) warning(/Mutation of 'w.h' while borrowed/)
        return r.v
    return 0


# the same through a callee replacing the containing record
def above_call(w: W) -> int32:  # tpyc: mir(conflict /replacement/)
    h = w.h
    if h.o is not None:
        r = h.o
        empty_above(w)  # tpyc: warning(/may free the old value of 'w.h.o' or overwrite it in place/)
        return r.v
    return 0


def empty_refill(h: H) -> None:  # tpyc: mir(covered) mir_summary(known)
    h.o = None
    h.o = P(1)


# a callee that empties then refills: its stored alternatives union to either
def w_empty_refill(h: H) -> int32:  # tpyc: mir(conflict /replacement/)
    if h.o is not None:
        r = h.o
        empty_refill(h)  # tpyc: warning(/may free the old value of 'h.o' or overwrite it in place/)
        return r.v
    return 0


# BUGS.md#opt-field-payload-local-cleared-by-call: lowers, but a later pass may rebind y = h.o,
# so the stale read is not shown and nothing warns
def narrowed_then_loop(h: H) -> int32:  # tpyc: mir(conflict /replacement/)
    y = h.p
    for i in range(3):
        if h.o is not None:
            y = h.o
        if i == 1:
            clear(h)  # tpyc: ok
    return y.v


# one parameter's payload held while another's field is emptied: a no-alias obligation
# naming the payload's path against the summary's own write
def holding(a: H, b: H) -> int32:  # tpyc: mir(conflict /alias_obligation/) mir_summary(known)
    if a.o is not None:
        r = a.o
        b.o = None
        return r.v
    return 0


def holding_same(h: H) -> int32:  # tpyc: mir(conflict /argument_alias/)
    n = holding(h, h)  # tpyc: warning(/argument 'h' contains 'h.o', which 'holding' modifies through parameter 'b'/)
    return n


# Equal places are not one object, and copy() is mostly materialized before it is assigned:
# these copies onto "the same" place warn (BUGS.md#opt-field-self-copy-false-warnings).
# two elements of one list, which MIR spells as one place: the record overwritten in place
def element_copy(ps: list[P]) -> int32:  # tpyc: mir(conflict /replacement/)
    p = ps[0]
    ps[0] = copy(ps[1])  # tpyc: warning(/this assignment may overwrite what 'p' refers to in place/)
    return p.v


# the same for a str element: the viewed buffer may be freed
def element_view(xs: list[str], i: int32, j: int32) -> StrView:  # tpyc: mir(conflict /replacement/)
    v: StrView = xs[i]
    xs[i] = xs[j]  # tpyc: warning(/this assignment may free the element 'v' still views/)
    return v


class Ps:
    ps: list[P]

    def __init__(self) -> None:
        self.ps = [P(1), P(9)]


# a field assigned an explicit copy of itself: the list's old buffer is freed
def explicit_self_copy(h: Ps) -> int32:  # tpyc: mir(conflict /replacement/)
    p = h.ps[0]
    h.ps = copy(h.ps)  # tpyc: warning(/this assignment frees the old value of 'h.ps', but 'p' still refers to it/) warning(/Mutation of 'h.ps' while borrowed/)
    return p.v


# the same for a record field: overwritten in place
def explicit_self_copy_record(h: H) -> int32:  # tpyc: mir(conflict /replacement/)
    r = h.p
    h.p = copy(h.p)  # tpyc: warning(/this assignment overwrites 'h.p' in place, but 'r' still refers to it/) warning(/Mutation of 'h.p' while borrowed/)
    return r.v


# the same for the Optional field, whose copy renders a plain self-assignment: a known false
# warning (BUGS.md#opt-field-self-copy-false-warnings), as for `h.o = h.o`
def explicit_self_copy_optional(h: H) -> int32:  # tpyc: mir(conflict /replacement/)
    r = P(0)
    if h.o is not None:
        r = h.o
    h.o = copy(h.o)  # tpyc: warning(/this assignment may free the old value of 'h.o' or overwrite it in place/) warning(/Mutation of 'h.o' while borrowed/)
    return r.v


def main() -> None:
    print("writes holding", holding(H(1), H(2)))
    # The unwarned shapes: a payload write the holder sees, as in CPython.
    h = H(1)
    if h.o is not None:
        r = h.o
        r.v = 3
        print("writes seen", h.o.v)
