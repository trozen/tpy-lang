# More holders of a payload: a published payload return, a view inside the payload, readonly and
# local-record receivers, one sum written through two aliasing parameters, a local Optional record
# refilled under a holder. Warned bodies: never run.
from tpy import StrView, int32, readonly
from fixtures import H, P, W, clear


def payload_of(h: H) -> P:  # tpyc: mir(covered) mir_summary(known)
    if h.o is not None:
        return h.o
    return h.p


# a caller holds the published payload return across the field emptied directly, then by a callee;
# sema knows only that 'r' borrows from 'h' (the call lends from its argument), so its borrow
# warning names the whole 'h', the MIR one what the assignment does to the payload 'r' refers to
def ret_then_clear(h: H) -> int32:  # tpyc: mir(conflict /replacement/)
    r = payload_of(h)
    h.o = None  # tpyc: warning(/this assignment frees the old value of 'h.o', and 'r' may still refer to it/) warning(/Mutation of 'h' while borrowed/)
    return r.v


def ret_then_call(h: H) -> int32:  # tpyc: mir(conflict /replacement/)
    r = payload_of(h)
    clear(h)  # tpyc: warning(/this call frees the old value of 'h.o', and 'r' may still refer to it/)
    return r.v


class S:
    s: str

    def __init__(self, s: str) -> None:
        self.s = s


class SH:
    o: S | None

    def __init__(self, s: str) -> None:
        self.o = S(s)


# a view of an owned str inside the payload: a refill assigns in place and so replaces the viewed
# str -- its buffer freed when on the heap, overwritten when short -- and the warning states the
# worst case; sema's borrow warning on the same line is its own check, the MIR one the lifetime's
def view_refill(h: SH) -> None:  # tpyc: mir(conflict /replacement/)
    if h.o is not None:
        r = h.o
        v: StrView = r.s
        h.o = S("new")  # tpyc: warning(/this assignment frees the old value of 'h.o', but 'v' still views it/) warning(/Mutation of 'h.o' while borrowed/)
        print("holders view", v)


# a readonly receiver: the holder only reads
def read_ro(h: readonly[H]) -> int32:  # tpyc: mir(covered) mir_summary(known)
    if h.o is not None:
        r = h.o  # tpyc: mir_borrows(r, h.o[payload])
        return r.v
    return 0


# a local record receiver: a write through the holder is seen through the field
def local_rec(n: int32) -> int32:  # tpyc: mir(covered)
    h = H(n)
    if h.o is not None:
        r = h.o  # tpyc: mir_borrows(r, h.o[payload])
        r.v = 4
        return h.o.v
    return 0


def local_clear(n: int32) -> int32:  # tpyc: mir(conflict /replacement/)
    h = H(n)
    if h.o is not None:
        r = h.o
        clear(h)  # tpyc: warning(/this call frees the old value of 'h.o', but 'r' still refers to it/)
        return r.v
    return 0


def empty_then_fill(w: W, h: H) -> None:  # tpyc: mir(conflict /alias_obligation/) mir_summary(known)
    w.h.o = None
    h.o = P(5)


# one sum emptied through one parameter and refilled through another: the caller unions them by
# the storage both reach, so neither place's warning says the payload is overwritten in place
def aliased(w: W) -> int32:  # tpyc: mir(conflict /replacement/)
    h = w.h
    if h.o is not None:
        r = h.o
        empty_then_fill(w, h)  # tpyc: warning(/free.* the old value of 'w.h.o'/) warning(/free.* the old value of 'h.o'/)
        return r.v
    return 0


# the same callee with a local record refilled: nothing that write reaches is 'w.h.o', so the
# emptying stays a free
def not_aliased(w: W) -> int32:  # tpyc: mir(conflict /replacement/)
    h = w.h
    h2 = H(1)
    if h.o is not None:
        r = h.o
        empty_then_fill(w, h2)  # tpyc: warning(/this call frees the old value of 'w.h.o', but 'r' still refers to it/)
        return r.v
    return 0


class W2:
    a: H
    b: H

    def __init__(self, n: int32) -> None:
        self.a = H(n)
        self.b = H(n)


def empty_then_fill_two(x: H, y: H) -> None:  # tpyc: mir_summary(known)
    x.o = None
    y.o = P(5)


# a refill through a parameter that may be either field: each storage it may reach takes the
# refill alone, so the held 'w.b.o' is overwritten in place, never freed through 'w.a.o''s emptying
def either_field(w: W2, c: bool) -> int32:  # tpyc: mir(conflict /replacement/)
    h = w.a
    if not c:
        h = w.b
    b = w.b
    if b.o is not None:
        r = b.o
        empty_then_fill_two(w.a, h)  # tpyc: warning(/this call may overwrite what 'r' refers to in place/)
        return r.v
    return 0


# a local Optional record refilled each pass while a holder keeps the payload of its field: the
# warning names the local's field, as a field write's does; 'o' binds one hoisted storage that each
# pass assigns into, so 'r' sees the new record's field where CPython's keeps the old P -- sema's
# rebind warning is that divergence, the MIR one the lifetime of the payload 'r' refers to
def local_refill_held(n: int32) -> int32:  # tpyc: mir(conflict /replacement/)
    o: H | None = None
    r = P(0)
    total = 0
    i = 0
    while i < n:
        o = H(i)  # tpyc: warning(/this assignment may free the old value of 'o.o' or overwrite it in place, and 'r' may still refer to it/) warning(/'r' will not keep the object it was given/)
        total += r.v
        if o is not None and o.o is not None:
            r = o.o
        i += 1
    return total + r.v


def main() -> None:
    print("holders readonly", read_ro(H(3)))
    print("holders local", local_rec(1))
