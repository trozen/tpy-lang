# A protocol's generated concept probes each method at the PARAM FORM the
# protocol's own signature declares, so an implementation whose parameter is a
# mutable reference conforms. One section per reference-typed parameter form a
# protocol can declare; each mutates through the parameter and prints the
# caller's own object afterwards, so a silent copy at the boundary would show.
from typing import Optional, Protocol
from tpy import dynamic, int32, readonly


class Rec:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Other:
    tag: int32

    def __init__(self, tag: int32) -> None:
        self.tag = tag


class Holder:
    m: Rec

    def __init__(self, n: int32) -> None:
        self.m = Rec(n)


class Shapes(Protocol):
    # a MUTABLE record parameter: the conformer's param is `Rec&`
    def bump(self, r: Rec) -> int32: ...  # tpyc: ok
    # a mutable container parameter
    def grow(self, xs: list[int32]) -> int32: ...  # tpyc: ok
    # a readonly parameter keeps the const form
    def total(self, xs: readonly[list[int32]]) -> int32: ...  # tpyc: ok
    # a pointer-repr Optional parameter
    def opt_bump(self, r: Optional[Rec]) -> int32: ...  # tpyc: ok
    # a pointer-variant union parameter
    def uni_bump(self, u: Rec | Other) -> int32: ...  # tpyc: ok
    # value / view parameter forms are unchanged by the param-form probe
    def label(self, s: str, k: int32) -> int32: ...  # tpyc: ok


class Impl:
    def __init__(self) -> None:
        pass

    def bump(self, r: Rec) -> int32:
        r.n += 1
        return r.n

    def grow(self, xs: list[int32]) -> int32:
        xs.append(9)
        return int32(len(xs))

    def total(self, xs: readonly[list[int32]]) -> int32:
        return int32(len(xs))

    def opt_bump(self, r: Optional[Rec]) -> int32:
        if r is None:
            return -1
        r.n += 10
        return r.n

    def uni_bump(self, u: Rec | Other) -> int32:
        if isinstance(u, Rec):
            u.n += 100
            return u.n
        u.tag += 100
        return u.tag

    def label(self, s: str, k: int32) -> int32:
        return int32(len(s)) + k


@dynamic
class Dyn(Protocol):
    # the same mutable record parameter, dispatched through a vtable
    def bump(self, r: Rec) -> int32: ...  # tpyc: ok
    # conformance only: the implementation returns a borrow of the parameter,
    # which infers a const method and so a `Holder&` parameter
    def pick(self, h: Holder) -> Rec: ...  # tpyc: ok
    def tag(self) -> int32: ...


class DynImpl:
    def __init__(self) -> None:
        pass

    def bump(self, r: Rec) -> int32:
        r.n += 1
        return r.n

    def pick(self, h: Holder) -> Rec:
        return h.m

    def tag(self) -> int32:
        return 7


class Caller:
    seen: int32

    def __init__(self) -> None:
        self.seen = 0

    # the same call from a METHOD position
    def drive(self, s: Shapes, r: Rec) -> None:
        self.seen = s.bump(r)


def free_bump(s: Shapes, r: Rec) -> int32:
    return s.bump(r)


def free_grow(s: Shapes, xs: list[int32]) -> int32:
    return s.grow(xs)


def free_total(s: Shapes, xs: readonly[list[int32]]) -> int32:
    return s.total(xs)


def free_opt(s: Shapes, r: Optional[Rec]) -> int32:
    return s.opt_bump(r)


def free_uni(s: Shapes, u: Rec | Other) -> int32:
    return s.uni_bump(u)


def main() -> None:
    impl = Impl()

    r = Rec(1)
    seen = free_bump(impl, r)
    print("rec", seen, r.n)

    xs: list[int32] = [1, 2]
    grown = free_grow(impl, xs)
    print("list", grown, len(xs))

    ro: list[int32] = [1, 2, 3]
    print("ro_list", free_total(impl, ro), len(ro))

    o = Rec(5)
    opted = free_opt(impl, o)
    print("opt", opted, o.n)

    u = Rec(7)
    united = free_uni(impl, u)
    print("union", united, u.n)

    print("value", impl.label("abc", 2))

    c = Caller()
    m = Rec(20)
    c.drive(impl, m)
    print("method", c.seen, m.n)

    d: Dyn = DynImpl()
    dr = Rec(30)
    bumped = d.bump(dr)
    print("dyn", bumped, dr.n)
    print("dyn_borrow", d.tag())


main()
