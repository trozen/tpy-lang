# A guarded arm of a union `match` whose GUARD reads the subject: the read
# renames to the arm's `__case_N` alias, which the group scope declares ahead
# of the guard. Every section mutates the subject after the guard and the
# caller observes the change, so a silent copy cannot pass.
from tpy import int32


class Rec:
    n: int32
    q: int32

    def __init__(self, n: int32, q: int32) -> None:
        self.n = n
        self.q = q


class Other:
    m: int32

    def __init__(self, m: int32) -> None:
        self.m = m


class Third:
    t: int32

    def __init__(self, t: int32) -> None:
        self.t = t


class Gate:
    def __enter__(self) -> "Gate":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


# free function: the guard reads the narrowed subject
def free_fn(v: Rec | Other) -> int32:
    match v:
        case Rec() if v.n > 0:  # tpyc: ok
            v.n = v.n + 100
            return v.n
        case _:
            return 0


# free function: a field CONDITION beside a guard that reads the subject
def field_cond(v: Rec | Other) -> int32:
    match v:
        case Rec(n=1) if v.q > 0:  # tpyc: ok
            v.q = v.q + 100
            return v.q
        case _:
            return 0


# free function: an `as` binding beside a guard that reads the subject
def as_bind(v: Rec | Other) -> int32:
    match v:
        case Rec() as r if v.n > 0:  # tpyc: ok
            r.n = r.n + 100
            return v.n
        case _:
            return 0


# free function: two entries on one variant index, the first guarded
def same_index(v: Rec | Other | Third) -> int32:
    match v:
        case Rec() if v.n > 5:  # tpyc: ok
            v.n = v.n + 100
            return v.n
        case Rec(n=k):
            return k
        case _:
            return 0


# free function: a union that also carries None
def with_none(v: Rec | None | Other) -> int32:
    match v:
        case Rec() if v.n > 0:  # tpyc: ok
            v.n = v.n + 100
            return v.n
        case None:
            return -1
        case _:
            return 0


class Holder:
    seen: int32

    # constructor: the match runs during __init__
    def __init__(self, v: Rec | Other) -> None:
        self.seen = 0
        match v:
            case Rec() if v.n > 0:  # tpyc: ok
                v.n = v.n + 100
                self.seen = v.n
            case _:
                self.seen = 0

    # method: the guard reads the subject parameter
    def bump(self, v: Rec | Other) -> int32:
        match v:
            case Rec() if v.n > 0:  # tpyc: ok
                v.n = v.n + 100
                return v.n
            case _:
                return 0


# context-manager body and try/finally
def in_blocks(v: Rec | Other) -> int32:
    total = 0
    with Gate():
        match v:
            case Rec() if v.n > 0:  # tpyc: ok
                v.n = v.n + 100
                total += v.n
            case _:
                pass
    try:
        match v:
            case Rec() if v.n > 0:  # tpyc: ok
                v.n = v.n + 1000
                total += v.n
            case _:
                pass
    finally:
        total += 1
    return total


# closure: the match lives in a nested def
def in_closure(v: Rec | Other) -> int32:
    def inner() -> int32:
        match v:
            case Rec() if v.n > 0:  # tpyc: ok
                v.n = v.n + 100
                return v.n
            case _:
                return 0
    return inner()


# match arm: an inner union match inside an outer arm's body
def in_match_arm(v: Rec | Other, sel: int32) -> int32:
    match sel:
        case 1:
            match v:
                case Rec() if v.n > 0:  # tpyc: ok
                    v.n = v.n + 100
                    return v.n
                case _:
                    return 0
        case _:
            return 0


def main() -> None:
    a = Rec(1, 2)
    print("free_fn", free_fn(a), a.n)
    b = Rec(1, 2)
    print("field_cond", field_cond(b), b.q)
    c = Rec(3, 0)
    print("as_bind", as_bind(c), c.n)
    d = Rec(7, 0)
    print("same_index", same_index(d), d.n)
    e = Rec(2, 0)
    print("with_none", with_none(e), e.n)
    print("with_none_none", with_none(None))
    f = Rec(4, 0)
    print("ctor", Holder(f).seen, f.n)
    g = Rec(5, 0)
    gu: Rec | Other = g
    other: Rec | Other = Other(0)
    holder = Holder(other)
    print("method", holder.bump(gu), g.n)
    h = Rec(6, 0)
    print("in_blocks", in_blocks(h), h.n)
    i = Rec(8, 0)
    print("in_closure", in_closure(i), i.n)
    j = Rec(9, 0)
    print("in_match_arm", in_match_arm(j, 1), j.n)
    print("other", free_fn(Other(1)))


main()
