# The payload held through an Optional local (`o = h.o`, narrowed by `if o is None`), across a call
# or an assignment that empties or refills the field. Every body is warned: never run.
from tpy import int32
from fixtures import H, P, clear, refill


# a callee empties the field: the payload 'o' points at is destroyed
def held_clear(h: H) -> int32:  # tpyc: mir(conflict /replacement/)
    o = h.o
    if o is None:
        return -1
    clear(h)  # tpyc: warning(/this call frees the old value of 'h.o', but 'o' still refers to it/)
    return o.v


# a callee refills the field: assigned in place, so 'o' sees the new value (CPython keeps the old P)
def held_refill(h: H) -> int32:  # tpyc: mir(conflict /replacement/)
    o = h.o
    if o is None:
        return -1
    refill(h)  # tpyc: warning(/this call overwrites 'h.o' in place, but 'o' still refers to it/)
    return o.v


# the field emptied directly: sema drops the narrowing of 'o', so MIR does not analyze the body and
# only sema's borrow warning shows (BUGS.md#opt-holder-narrowing-dropped-after-field-write)
def held_none(h: H) -> int32:  # tpyc: mir(uncovered /unproven pointer field read/)
    o = h.o
    if o is None:
        return -1
    h.o = None  # tpyc: warning(/Mutation of 'h.o' while borrowed/)
    return o.v  # tpyc: warning(/Potential None access on optional value/)


# the field refilled directly: overwritten in place, as by the callee
def held_assign(h: H) -> int32:  # tpyc: mir(conflict /replacement/)
    o = h.o
    if o is None:
        return -1
    h.o = P(5)  # tpyc: warning(/this assignment overwrites 'h.o' in place, but 'o' still refers to it/) warning(/Mutation of 'h.o' while borrowed/)
    return o.v


def main() -> None:
    print("local_holder compiled")
