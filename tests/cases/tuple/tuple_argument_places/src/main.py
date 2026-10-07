# A tuple at an OWNING argument place -- an `Own[tuple]` parameter of a free
# function or a constructor, and the `tuple[Own[A], Own[A]]` spelling of the
# same place -- takes the field write's storage conversion: a literal spells
# its storage, a name moves at its last use and copies (warned) while live, a
# borrowing source lifts. An owned tuple ELEMENT passed to `Own[T]` moves at
# the tuple's last use, as `return p[0]` does. The prints read what both
# runtimes agree on once a copy is warned.
from tpy import int32, Own, copy


class Box:
    def __init__(self, n: int32) -> None:
        self.n = n


class H:
    t: tuple[Box, int32]

    def __init__(self) -> None:
        self.t = (Box(0), 0)


class HN:
    t: tuple[Box, int]

    def __init__(self) -> None:
        self.t = (Box(0), 0)


class K:
    t: tuple[Box, int32]

    # (the member-init moves `t`; the warning is
    # BUGS.md#own-tuple-param-field-store-warns)
    def __init__(self, t: Own[tuple[Box, int32]]) -> None:
        self.t = t


class KB:
    b: Box

    def __init__(self, b: Own[Box]) -> None:
        self.b = b


class P:
    def __init__(self, x: int32) -> None:
        self.x = x


def make_mixed(b: Box) -> tuple[Own[Box], Box]:
    return (Box(1), b)


def keep(xs: list[tuple[Box, int32]], t: Own[tuple[Box, int32]]) -> int32:
    xs.append(t)
    return len(xs)


def keep_m(xs: list[tuple[Box, Box]], t: Own[tuple[Box, Box]]) -> int32:
    xs.append(t)
    return len(xs)


def keep2(t: tuple[Own[Box], Own[Box]]) -> int32:  # tpyc: warning(/never consumed/)
    return t[0].n + t[1].n


def sink(keep: list[Box], b: Own[Box]) -> int32:
    keep.append(b)
    return keep[-1].n


def take_opt(t: Own[tuple[P | None, P | None]]) -> int32:
    a, b = t
    return (a.x if a is not None else 0) + (b.x if b is not None else 0)


def nested_borrow(t: tuple[int32, tuple[int32, Box]]) -> int32:
    # (an element of a nested tuple param does not read back yet:
    # BUGS.md#nested-storage-tuple-element-read)
    return 1


def nested_own(t: Own[tuple[int32, tuple[int32, Box]]]) -> int32:
    return 2


def flat_borrow(t: tuple[int32, Box]) -> int32:
    return t[1].n


# free function: a literal-bound local moves at its last use
def local_last_use(xs: list[tuple[Box, int32]]) -> int32:
    t = (Box(5), 2)
    return keep(xs, t)  # tpyc: ok


# free function: the same local still live copies (warned)
def local_live(xs: list[tuple[Box, int32]]) -> int32:
    t = (Box(6), 2)
    n = keep(xs, t)  # tpyc: warning(/copies Box into owned storage \(tuple element 0\)/)
    return n + t[0].n


# free function: an alias read in the SAME statement as the pass keeps the
# local live, so the slot copies (warned) and the alias reads the original
def local_alias_same_stmt(xs: list[tuple[Box, int32]]) -> int32:
    t = (Box(7), 2)
    q = t
    n = keep(xs, t) + q[0].n  # tpyc: warning(/copies Box into owned storage \(tuple element 0\)/)
    return n


# free function: an `Own[tuple]` parameter forwarded at its last use moves
def forward_own(xs: list[tuple[Box, int32]], t: Own[tuple[Box, int32]]) -> int32:
    return keep(xs, t)  # tpyc: ok


# free function: ... and appended at its last use moves
def append_own(xs: list[tuple[Box, int32]], t: Own[tuple[Box, int32]]) -> None:
    xs.append(t)  # tpyc: ok


# free function: a mixed call lifts, copying its borrowed element (warned)
def mixed_call(xs: list[tuple[Box, Box]], b: Box) -> int32:
    return keep_m(xs, make_mixed(b))  # tpyc: warning(/tuple element 1\)/)


# free function: a tuple FIELD read is a stored lvalue the slot copies
def field_read(xs: list[tuple[Box, int32]], h: H) -> int32:
    return keep(xs, h.t)  # tpyc: warning(/tuple element 0\)/)


# free function: a literal spells its storage; a borrowed member copies
# (warned), an `Own` parameter member moves in
def literal(xs: list[tuple[Box, int32]], b: Box, q: Own[Box]) -> int32:
    keep(xs, (b, 1))  # tpyc: warning(/argument 't' tuple element 0\)/)
    return keep(xs, (q, 2))  # tpyc: ok


# free function: a borrowed local lifts with the warned copy; `copy(t)`
# declares it
def borrowed_local(xs: list[tuple[Box, int32]], b: Box) -> int32:
    t = (b, 3)
    keep(xs, t)  # tpyc: warning(/copies Box into owned storage \(tuple element 0\)/)
    return keep(xs, copy(t))  # tpyc: ok


# free function: an owned element moves at the tuple's last use
def element_last_use(ks: list[Box], p: tuple[Own[Box], int32]) -> int32:
    return sink(ks, p[0])  # tpyc: ok


# free function: ... and copies (warned) while the tuple is still live
def element_live(ks: list[Box], p: tuple[Own[Box], int32]) -> int32:  # tpyc: warning(/never consumed/)
    n = sink(ks, p[0])  # tpyc: warning(/copies Box into owned storage \(tuple element 0\)/)
    return n + p[0].n


# method: the owned element of a MIXED parameter moves at its last use too
class Holder:
    k: int32

    def __init__(self) -> None:
        self.k = 1

    def mixed_element(self, ks: list[Box], p: tuple[Own[Box], Box]) -> int32:
        return sink(ks, p[0]) + self.k  # tpyc: ok


# free function: the per-element spelling of an owned tuple: a move at the
# last use, the decay copy while live (warned twice:
# BUGS.md#own-tuple-amp-slot-live-copy-double-warning)
def amp_slot() -> int32:
    t = (Box(7), Box(8))
    u = (Box(9), Box(10))
    n = keep2(t)  # tpyc: ok
    m = keep2(u)  # tpyc: warning(/copies tuple\[Own\[Box\], Own\[Box\]\] into owned storage/)
    return n + m + u[0].n


# free function: an Optional member lands whole (`ptr_to_optional`), None
# included
def optional_member(o: P | None) -> int32:
    return take_opt((o, o))  # tpyc: warning(/tuple element 0\)/) warning(/tuple element 1\)/)


# free function: a nested literal at a borrowing parameter borrows a level
# down; at an owning one it stores
def nested(c: Box) -> int32:
    return nested_borrow((1, (2, c))) + nested_own((1, (2, c)))  # tpyc: warning(/tuple element 1.1\)/)


# free function: a literal over an un-mutated parameter borrows it const
def const_borrow(c: Box) -> int32:
    return flat_borrow((1, c))  # tpyc: ok


# constructor: the same sources at an `Own[tuple]` ctor slot
def ctor_sources(h: H) -> int32:
    t = (Box(11), 1)
    k1 = K(t)  # tpyc: ok
    k2 = K((Box(12), 2))  # tpyc: ok
    k3 = K(h.t)  # tpyc: warning(/tuple element 0\)/)
    k1.t[0].n += 100
    return k1.t[0].n + k2.t[0].n + k3.t[1]


# constructor: an owned element at an `Own[Box]` ctor slot moves
def ctor_element(p: tuple[Own[Box], int32]) -> Own[KB]:
    return KB(p[0])  # tpyc: ok


# free function: a BigInt-element tuple NAME stores into a field and a dict
def bigint_name(h: HN, d: dict[int32, tuple[Box, int]], b: Box) -> None:
    t = (b, 3)
    h.t = t  # tpyc: warning(/copies Box into field \(tuple element 0\)/)
    d[0] = t  # tpyc: warning(/copies Box into container \(tuple element 0\)/)


def main() -> None:
    # Each section stores into a list of its own; the moved-in tuple is the
    # list's own, so a write through it reads back.
    xa: list[tuple[Box, int32]] = []
    print("local_last_use", local_last_use(xa))
    x0 = xa[0]
    x0[0].n += 100
    print("local_last_use stored", x0[0].n, x0[1])
    xb: list[tuple[Box, int32]] = []
    print("local_live", local_live(xb))
    xq: list[tuple[Box, int32]] = []
    print("local_alias_same_stmt", local_alias_same_stmt(xq))
    xc: list[tuple[Box, int32]] = []
    print("forward_own", forward_own(xc, (Box(20), 3)))
    append_own(xc, (Box(21), 4))
    x1 = xc[1]
    x1[0].n += 100
    print("append_own", x1[0].n, len(xc))
    ms: list[tuple[Box, Box]] = []
    n = mixed_call(ms, Box(30))
    m0 = ms[0]
    print("mixed_call", n, m0[0].n, m0[1].n)
    h = H()
    xd: list[tuple[Box, int32]] = []
    n = field_read(xd, h)
    x2 = xd[0]
    print("field_read", n, x2[0].n)
    xe: list[tuple[Box, int32]] = []
    n = literal(xe, Box(40), Box(41))
    x3 = xe[1]
    print("literal", n, x3[0].n)
    xf: list[tuple[Box, int32]] = []
    n = borrowed_local(xf, Box(42))
    x4 = xf[1]
    print("borrowed_local", n, x4[0].n, x4[1])
    ks: list[Box] = []
    print("element_last_use", element_last_use(ks, (Box(50), 1)))
    ks[0].n += 100
    print("element_last_use stored", ks[0].n)
    print("element_live", element_live(ks, (Box(51), 1)))
    nb = Box(53)
    print("method_mixed_element", Holder().mixed_element(ks, (Box(52), nb)))
    ks[2].n += 100
    print("method_mixed_element stored", ks[2].n, nb.n)
    print("amp_slot", amp_slot())
    print("optional_member", optional_member(P(7)), optional_member(None))
    print("nested", nested(Box(60)))
    print("const_borrow", const_borrow(Box(61)))
    print("ctor_sources", ctor_sources(h))
    kb = ctor_element((Box(70), 1))
    kb.b.n += 100
    print("ctor_element", kb.b.n)
    hn = HN()
    d: dict[int32, tuple[Box, int]] = {}
    bigint_name(hn, d, Box(80))
    d0 = d[0]
    print("bigint_name", hn.t[0].n, hn.t[1], d0[1])


main()
