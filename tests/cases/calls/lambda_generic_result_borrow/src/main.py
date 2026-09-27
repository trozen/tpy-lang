# A lambda at a generic result slot (key=, map's function) keeps returning a
# borrow when the borrow is rooted outside the lambda body: a parameter's
# field, or an argument a method on a temporary hands back; a scalar read
# through a temporary is returned by value. Only a borrow into a value the
# body created is rejected (error_lambda_key_temp_borrow,
# error_lambda_key_temp_field).
from __future__ import annotations
from tpy import int32, Own


class Inner:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v

    def __lt__(self, o: Inner) -> bool:
        return self.v < o.v


class Holder:
    inner: Inner

    def __init__(self) -> None:
        self.inner = Inner(7)


class Rec:
    n: int32
    inner: Inner

    def __init__(self, n: int32) -> None:
        self.n = n
        self.inner = Inner(n)

    def shared(self, h: Holder) -> Inner:
        return h.inner

    def get_inner(self) -> Inner:
        return self.inner


def mk(r: Rec) -> Own[Rec]:
    return Rec(r.n * 10)


class Team:
    members: list[Rec]

    def __init__(self) -> None:
        self.members = [Rec(5), Rec(4), Rec(6)]

    def ranked(self) -> None:
        # method: the key borrows the element parameter's field.
        ys = sorted(self.members, key=lambda r: r.inner)  # tpyc: ok
        print("method", [y.n for y in ys])


def main() -> None:
    rs = [Rec(2), Rec(1), Rec(3)]
    # free function: sorted/min key borrowing the parameter's field.
    ys = sorted(rs, key=lambda r: r.inner)  # tpyc: ok
    print("free", [y.n for y in ys])
    a = rs[0]
    b = rs[1]
    print("free_min", min(a, b, key=lambda r: r.inner).n)  # tpyc: ok

    Team().ranked()

    # map: the element borrow aliases the source, so writes reach rs.
    for i in map(lambda r: r.inner, rs):  # tpyc: ok
        i.v += 100
    print("map_alias", [r.inner.v for r in rs])

    # map over a method on a temporary that returns its ARGUMENT's field:
    # the borrow is rooted in h, not in mk(r), so writes reach h.inner.
    h = Holder()
    for j in map(lambda r: mk(r).shared(h), rs):  # tpyc: ok
        j.v += 1
    print("map_arg_rooted", h.inner.v)

    # a key that builds a fresh value returns it by value.
    zs = sorted(rs, key=lambda r: Inner(-r.n))  # tpyc: ok
    print("fresh_key", [z.n for z in zs])

    # a scalar field of a temporary, and a scalar read through a borrow of
    # one, are copied out before the temporary dies.
    ws = sorted(rs, key=lambda r: mk(r).n)  # tpyc: ok
    print("temp_scalar_field", [w.n for w in ws])
    vs = sorted(rs, key=lambda r: -mk(r).get_inner().v)  # tpyc: ok
    print("temp_scalar_via_borrow", [v.n for v in vs])


main()
