# A narrowed Optional field's payload is an ordinary MIR place, warned only where freed or
# overwritten while held; read sections mutate it after binding, so a silent copy would show.
from tpy import int32
from fixtures import H, P
import part_holders
import part_positions
import part_writes
import part_refused
import part_local_holder


# a callee writing a field of the payload through a holder of it: published at the payload's path
def setv(h: H) -> None:  # tpyc: mir(covered) mir_summary(known)
    if h.o is not None:
        r = h.o  # tpyc: mir_borrows(r, h.o[payload])
        r.v = 5


# the payload's field written in place through the narrowed field
def write(h: H) -> None:  # tpyc: mir(covered) mir_summary(known)
    if h.o is not None:
        h.o.v = 6


def setv_m(h: H) -> None:  # tpyc: mir(covered) mir_summary(known)
    if h.o is not None:
        h.o.reset()


# a scalar written inside the held payload replaces no storage the holder refers to: no warning
def caller_scalar(h: H) -> int32:  # tpyc: mir(covered)
    if h.o is not None:
        r = h.o
        setv(h)  # tpyc: ok
        return r.v
    return 0


def caller_method(h: H) -> int32:  # tpyc: mir(covered)
    if h.o is not None:
        r = h.o
        setv_m(h)  # tpyc: ok
        return r.v
    return 0


# two holders of one payload: a write through one is seen through the other, as in CPython
def local_two(h: H) -> int32:  # tpyc: mir(covered)
    if h.o is not None:
        r = h.o
        q = h.o
        q.v = 7  # tpyc: ok
        return r.v
    return 0


# the holder reseated from another field onto the payload on one path
def pick(h: H, c: bool) -> int32:  # tpyc: mir(covered)
    r = h.p
    if h.o is not None and c:
        r = h.o  # tpyc: ok
    r.v = 8
    return (h.o.v if h.o is not None else 0) + h.p.v


def main() -> None:
    h = H(1)
    write(h)
    print("core write", h.o.v if h.o is not None else -1)
    print("core scalar", caller_scalar(H(1)), caller_method(H(1)), local_two(H(1)))
    print("core pick", pick(H(1), True), pick(H(1), False))
    part_positions.main()
    part_writes.main()
    part_holders.main()
    part_refused.main()
    part_local_holder.main()


main()
