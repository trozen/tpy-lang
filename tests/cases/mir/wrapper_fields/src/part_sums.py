# Optional fields of a scalar or an owned leaf (`k: int32 | None`, `o: str | None`) and a union
# field of a scalar and an owned leaf (`u: int32 | str`): places of their record, stored inline.
# A narrowed `str | None` payload bound to a view is a THIR reject today, so no section holds one.
from tpy import int32, readonly
from fixtures import T, mutate


class S:
    o: str | None
    u: int32 | str
    k: int32 | None

    # constructor: each field engaged or emptied
    def __init__(self) -> None:  # tpyc: mir(covered)
        self.o = "a" * 3
        self.u = "b" * 3
        self.k = None

    # method: the fields rewritten through self
    def reset(self) -> None:  # tpyc: mir(covered) mir_summary(known)
        self.o = None  # tpyc: mir_write(self.o)
        self.u = 4  # tpyc: mir_write(self.u)
        self.k = 5  # tpyc: mir_write(self.k)


def refill(h: S) -> None:  # tpyc: mir(covered) mir_summary(known)
    h.o = "z" * 2
    h.u = "c" * 2


# a narrowed owned-leaf payload read in place
def payload_len(h: S) -> int32:  # tpyc: mir(covered) mir_summary(known)
    if h.o is not None:
        return len(h.o)
    return 0


# reads through a readonly receiver
def ro_read(h: readonly[S]) -> int32:  # tpyc: mir(covered) mir_summary(known)
    if h.k is not None and h.o is not None:
        return h.k + len(h.o)
    return 0


# whole copies of another holder's fields
def copies(a: S, b: S) -> None:  # tpyc: mir(covered) mir_summary(known)
    a.o = b.o  # tpyc: mir_copy(a.o)
    a.u = b.u  # tpyc: mir_copy(a.u)
    a.k = b.k  # tpyc: mir_copy(a.k)


# a scalar Optional parameter copied into the field
def from_param(h: S, k: int32 | None) -> None:  # tpyc: mir(covered) mir_summary(known)
    h.k = k  # tpyc: mir_copy(h.k)


# presence kill: b may be a, so emptying b.k ends the proof that a.k is engaged (sema keeps
# it: BUGS.md#optional-field-narrowing-survives-alias-write)
def presence(a: S, b: S) -> int32:  # tpyc: mir(uncovered /presence proof/)
    if a.k is not None:
        b.k = None
        return a.k
    return 0


def empties_k(b: S) -> None:  # tpyc: mir(covered) mir_summary(known)
    b.k = None


# presence kill by a callee: the write it publishes ends a.k's proof as the direct one does
def presence_call(a: S, b: S) -> int32:  # tpyc: mir(uncovered /presence proof/)
    if a.k is not None:
        empties_k(b)
        return a.k
    return 0


# presence kept: emptying another field leaves a.k engaged
def presence_kept(a: S, b: S) -> int32:  # tpyc: mir(covered)
    if a.k is not None:
        b.o = None
        return a.k
    return 0


class F:
    s: str
    n: int32
    o: str | None
    k: int32 | None
    u: int32 | bool

    # constructor: Optional and union fields beside the plain fields they are filled from
    def __init__(self) -> None:  # tpyc: mir(covered)
        self.s = "p" * 2
        self.n = 7
        self.o = None
        self.k = None
        self.u = True


# Optional fields engaged from a plain field and a str parameter: each value is the payload built
# (s may view h.o, which the first write frees: a no-alias obligation)
def from_plain(h: F, s: str) -> None:  # tpyc: mir(conflict /alias_obligation/) mir_summary(known)
    h.o = h.s  # tpyc: mir_write(h.o)
    h.k = h.n  # tpyc: mir_write(h.k)
    h.o = s  # tpyc: mir_write(h.o)


# a union field engaged from a plain field: from_plain's union twin
def union_from_plain(h: F) -> None:  # tpyc: mir(covered) mir_summary(known)
    h.u = h.n  # tpyc: mir_write(h.u)


# a union local narrowed by its initializer, copied whole into a union field
def from_narrowed(h: F, x: int32) -> None:  # tpyc: mir(covered) mir_summary(known)
    loc: int32 | bool = x
    h.u = loc  # tpyc: mir_copy(h.u)


def eat_union(u: int32 | bool, s: str) -> int32:  # tpyc: mir(covered)
    return len(s)


# argument order: a writing call lifted into a union parameter
def order_union(h: T) -> int32:  # tpyc: mir(uncovered /order-sensitive eager operands/)
    return eat_union(mutate(h, False), h.t[1])


# argument order: a non-literal union argument writing nothing stays covered
def order_union_pure(h: T, n: int32) -> int32:  # tpyc: mir(covered)
    return eat_union(n, h.t[1])


def main() -> None:
    h = S()
    g = S()
    print("sums payload_len", payload_len(h))
    g.reset()
    copies(h, g)
    print("sums copies", h.o is None, h.k)
    refill(h)
    from_param(h, 9)
    print("sums ro_read", ro_read(h))
    print("sums presence", presence(h, g), presence_kept(h, g), presence_call(h, S()))
    f = F()
    from_plain(f, "q")
    from_narrowed(f, 3)
    print("sums from_plain", f.o, f.k)
    union_from_plain(f)
    print("sums union_from_plain", f.u)
    print("sums order_union", order_union(T()), order_union_pure(T(), 4))
