# A reference member nested inside an INNER value tuple is copied into owned
# storage exactly like a direct member, so the copy check walks nested tuples
# and names the depth in the element path. Every sink mutates through the
# stored copy then reads the original, so a regression to aliasing changes the
# printed numbers, not just the diagnostics. no_cpython because the copy IS
# the divergence under test.
from tpy import int32, copy


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Holder:
    q: tuple[int32, tuple[int32, P]]

    def __init__(self, c: P) -> None:
        self.q = (1, (2, c))  # tpyc: warning(/copies P into field \(tuple element 1.1\)/)


def via_list_literal(c: P) -> int32:
    xs: list[tuple[int32, tuple[int32, P]]] = [(1, (2, c))]  # tpyc: warning(/copies P into owned storage \(tuple element 1.1\)/)
    xs[0][1][1].n = 41
    return c.n


def via_inner_lvalue(c: P) -> int32:
    # The nested member arrives as a whole borrow-form tuple lvalue rather than
    # a literal, so the walk re-dispatches on the inner source shape. Kept live
    # past the store so last-use auto-move does not suppress the diagnostic.
    t = (2, c)
    xs: list[tuple[int32, tuple[int32, P]]] = [(1, t)]  # tpyc: warning(/copies P into owned storage \(tuple element 1.1\)/)
    xs[0][1][1].n = 42
    return t[1].n


def via_append(c: P) -> int32:
    # Two distinct copies of `c` on this path, so two diagnostics: the local
    # already owns its nested member (a nested reference gives no borrow form),
    # and the append copies again out of that storage.
    q: tuple[int32, tuple[int32, P]] = (1, (2, c))  # tpyc: warning(/copies P into owned storage \(tuple element 1.1\)/)
    xs: list[tuple[int32, tuple[int32, P]]] = []
    xs.append(q)  # tpyc: warning(/copies P into owned storage \(tuple element 1.1\)/)
    xs[0][1][1].n = 43
    return q[1][1].n


def via_field(h: Holder, c: P) -> int32:
    h.q = (9, (8, c))  # tpyc: warning(/copies P into field \(tuple element 1.1\)/)
    h.q[1][1].n = 44
    return c.n


def via_setitem(c: P) -> int32:
    # A subscript target names the sink "container" rather than "owned
    # storage"; the direct level at this sink is a separate silent gap.
    d: dict[int32, tuple[int32, tuple[int32, P]]] = {}
    d[0] = (9, (8, c))  # tpyc: warning(/copies P into container \(tuple element 1.1\)/)
    d[0][1][1].n = 47
    return c.n


def three_levels(c: P) -> int32:
    xs: list[tuple[int32, tuple[int32, tuple[int32, P]]]] = [(1, (2, (3, c)))]  # tpyc: warning(/copies P into owned storage \(tuple element 1.1.1\)/)
    xs[0][1][1][1].n = 45
    return c.n


def acknowledged(c: P) -> int32:
    # copy() at the nested member silences it, same spelling as at depth 0.
    xs: list[tuple[int32, tuple[int32, P]]] = [(1, (2, copy(c)))]  # tpyc: ok
    xs[0][1][1].n = 46
    return c.n


def fresh_member() -> int32:
    # A fresh rvalue nested member constructs in place -- nothing aliases, so
    # the walk must stay quiet. The inverse guard against over-triggering.
    xs: list[tuple[int32, tuple[int32, P]]] = [(1, (2, P(7)))]  # tpyc: ok
    return xs[0][1][1].n


def no_reference_member() -> int32:
    # Nested tuple of pure value elements: nothing to copy at any depth.
    xs: list[tuple[int32, tuple[int32, int32]]] = [(1, (2, 3))]  # tpyc: ok
    return xs[0][1][1]


def main() -> None:
    a = P(0)
    print("list:", via_list_literal(a), a.n)
    b = P(0)
    print("inner_lvalue:", via_inner_lvalue(b), b.n)
    d = P(0)
    print("append:", via_append(d), d.n)
    e = P(0)
    h = Holder(e)
    f = P(0)
    print("field:", via_field(h, f), f.n)
    j = P(0)
    print("setitem:", via_setitem(j), j.n)
    g = P(0)
    print("three:", three_levels(g), g.n)
    i = P(0)
    print("acknowledged:", acknowledged(i), i.n)
    print("fresh:", fresh_member())
    print("values:", no_reference_member())


main()
