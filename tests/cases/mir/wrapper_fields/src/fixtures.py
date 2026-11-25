# Records and helpers the wrapper-field sections share.
from tpy import int32, readonly


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

    # kept refusal: a tuple parameter copied into a tuple field holding a record
    def __init__(self, t: tuple[int32, P]) -> None:  # tpyc: mir(uncovered /unsupported container element/)
        self.t = t  # tpyc: warning(/copies P into field \(tuple element 1\)/)


class Ro:
    p: readonly[P | None]

    # constructor: a readonly Optional record field (a store to it is a sema error)
    def __init__(self, p: P | None) -> None:  # tpyc: mir(covered)
        self.p = p  # tpyc: warning(/copies P \| None into field/)


class Num:
    k: int32 | None

    # constructor: an Optional of a scalar, its payload stored inline
    def __init__(self, k: int32 | None) -> None:  # tpyc: mir(covered)
        self.k = k


def clear(h: H) -> None:  # tpyc: mir(covered) mir_summary(known)
    h.p = None


# free function: a narrowed read of the payload's field
def read(h: H) -> int32:  # tpyc: mir(covered) mir_summary(known)
    if h.p is not None:
        return h.p.x
    return 0


class HL:
    p: P | None
    q: P
    s: str

    def __init__(self, p: P | None, q: P, s: str) -> None:
        self.p = p  # tpyc: warning(/copies P \| None into field/)
        self.q = q  # tpyc: warning(/copies P into field/)
        self.s = s


class S:
    s: str

    def __init__(self, s: str) -> None:  # tpyc: mir(covered)
        self.s = s


class SH:
    o: S | None

    def __init__(self, s: str) -> None:  # tpyc: mir(covered)
        self.o = S(s)


class T:
    t: tuple[int32, str]

    # constructor: the field built member by member
    def __init__(self) -> None:  # tpyc: mir(covered)
        self.t = (1, "a" * 3)


def mutate(h: T, flag: bool) -> int32:  # tpyc: mir(covered) mir_summary(known)
    if flag:
        h.t = (2, "b" * 3)
    return 2
