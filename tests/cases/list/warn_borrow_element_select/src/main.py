# A select of container-element arms hands out the SAME element borrow the
# plain `r = rs[0]` decl does, so a later structural mutation must warn too.
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


def main() -> None:
    call_arm([Rec(1)], True)
    rs = [Rec(1), Rec(2), Rec(3)]
    both_element_arms(rs, True)
    two_containers([Rec(1)], [Rec(2)], False)
    optional_arm(rs, True)
    alias_arms([Rec(1)], [Rec(2)], True)
    value_elements([1, 2], True)


main()
