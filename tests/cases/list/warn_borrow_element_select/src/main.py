# A select of container-element arms hands out the SAME element borrow the
# plain `r = rs[0]` decl does, so a later structural mutation must warn too.
from __future__ import annotations

from tpy import int32, Own


class Rec:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def both_element_arms(rs: list[Rec], c: bool) -> None:
    """Both arms are elements of one container: the append warns."""
    r = rs[0] if c else rs[1]
    r.n += 1                       # the write lands on the picked element
    rs.append(Rec(9))              # tpyc: warning(/Mutation of 'rs' while borrowed/)
    print(rs[0].n, len(rs))


def two_containers(xs: list[Rec], ys: list[Rec], c: bool) -> None:
    """Arms in different containers: either one can be the live borrow, so both warn."""
    r = xs[0] if c else ys[0]
    r.n += 10
    xs.append(Rec(7))              # tpyc: warning(/Mutation of 'xs' while borrowed/)
    ys.append(Rec(8))              # tpyc: warning(/Mutation of 'ys' while borrowed/)
    print(xs[0].n, ys[0].n)


def optional_arm(rs: list[Rec], c: bool) -> None:
    """A None arm loans nothing, the element arm still does."""
    p = rs[0] if c else None       # tpyc: ok
    if p is not None:
        p.n += 100
    rs.append(Rec(5))              # tpyc: warning(/Mutation of 'rs' while borrowed/)
    print(rs[0].n)


def alias_arms(xs: list[Rec], ys: list[Rec], c: bool) -> None:
    """Whole-container arms are ALIAS borrows -- safe through a structural mutation."""
    picked = xs if c else ys
    xs.append(Rec(3))              # tpyc: ok
    print(len(picked))


def value_elements(ns: list[int32], c: bool) -> None:
    """A value element is copied out, so no borrow and no warning."""
    v = ns[0] if c else ns[1]
    ns.append(6)                   # tpyc: ok
    print(v)


def first(rs: list[Rec]) -> Rec:
    return rs[0]


def make(n: int32) -> Own[Rec]:
    return Rec(n)


def call_arm(rs: list[Rec], c: bool) -> None:
    """A borrow-returning call arm loans its callee's source, as the direct
    `r = first(rs)` does, whichever arm the other one is."""
    r = first(rs) if c else make(5)
    r.n += 1
    rs.append(Rec(4))              # tpyc: warning(/Mutation of 'rs' while borrowed/)
    print(rs[0].n, len(rs))


def nested_select(xs: list[Rec], ys: list[Rec], zs: list[Rec], c: bool,
                  d: bool) -> None:
    """A ternary nested in an arm: its arms are flattened, the innermost one
    included."""
    r = xs[0] if c else (ys[0] if d else zs[0])
    r.n += 20
    zs.append(Rec(6))              # tpyc: warning(/Mutation of 'zs' while borrowed/)
    print("nested_select", zs[0].n, len(zs))


class Tree:
    n: int32
    kids: list[Tree]

    def __init__(self, n: int32) -> None:
        self.n = n
        self.kids = []

    def __getitem__(self, i: int32) -> Tree:
        return self.kids[i]

    def append(self, t: Own[Tree]) -> None:
        self.kids.append(t)


def strongest_kind(rs: Tree, c: bool) -> None:
    """Two arms on one key, an element and the whole: the strongest kind
    (the element) is the loan, so the append warns where `rs if c else rs`
    would not."""
    r = rs[0] if c else rs
    r.n += 1
    rs.append(Tree(5))             # tpyc: warning(/Mutation of 'rs' while borrowed/)
    print("strongest_kind", rs.kids[0].n, len(rs.kids))


def strongest_kind_reversed(rs: Tree, c: bool) -> None:
    """The same arms in the other order: the element still wins."""
    r = rs if c else rs[0]
    r.n += 1
    rs.append(Tree(6))             # tpyc: warning(/Mutation of 'rs' while borrowed/)
    print("strongest_kind_reversed", rs.kids[0].n, len(rs.kids))


def whole_arms(rs: Tree, c: bool) -> None:
    """Both arms the whole tree: an alias, which the append does not
    invalidate."""
    r = rs if c else rs
    rs.append(Tree(7))             # tpyc: ok
    r.n += 1
    print("whole_arms", rs.n, len(rs.kids))


def main() -> None:
    call_arm([Rec(1)], True)
    rs = [Rec(1), Rec(2), Rec(3)]
    both_element_arms(rs, True)
    two_containers([Rec(1)], [Rec(2)], False)
    optional_arm(rs, True)
    alias_arms([Rec(1)], [Rec(2)], True)
    value_elements([1, 2], True)
    nested_select([Rec(1)], [Rec(2)], [Rec(3)], False, False)
    t = Tree(0)
    t.append(Tree(1))
    strongest_kind(t, True)
    strongest_kind_reversed(t, False)
    whole_arms(t, True)


main()
