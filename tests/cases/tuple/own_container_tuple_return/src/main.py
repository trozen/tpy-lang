# A tuple return whose elements are OWNED containers: the storage brace-init
# moves each member out, and the caller's unpack moves it back into a local.
# Ownership transfer is the intent here -- the callee's local is gone, so the
# post-unpack mutation shows the buffer survived the move rather than a copy.
from tpy import Int32, Own, nocopy


# @nocopy: an Own transfer that copied instead would fail to compile.
@nocopy
class Rec:
    v: Int32

    def __init__(self, v: Int32) -> None:
        self.v = v


def two_lists() -> tuple[Own[list[float]], Own[list[Int32]]]:
    a: list[float] = []
    b: list[Int32] = []
    a.append(1.0)
    b.append(2)
    return a, b  # tpyc: ok


def mixed() -> tuple[Own[list[float]], Int32]:
    a: list[float] = []
    a.append(1.0)
    return a, 7  # tpyc: ok


def dict_and_set() -> tuple[Own[dict[Int32, Int32]], Own[set[Int32]]]:
    d: dict[Int32, Int32] = {}
    s: set[Int32] = set()
    d[1] = 2
    s.add(3)
    return d, s  # tpyc: ok


def two_recs() -> tuple[Own[Rec], Own[Rec]]:
    p = Rec(1)
    q = Rec(2)
    return p, q  # tpyc: ok


def recs_and_count() -> tuple[Own[list[Rec]], Int32]:
    rs: list[Rec] = []
    rs.append(Rec(5))
    rs.append(Rec(6))
    n = len(rs)
    return rs, n


def main() -> None:
    rs, n = recs_and_count()
    rs[0].v += 100
    print(n, rs[0].v, rs[1].v)
    a, b = two_lists()
    a.append(3.0)
    b.append(3)
    print(len(a), len(b), a[1], b[1])
    c, n = mixed()
    c.append(9.0)
    print(len(c), n, c[1])
    d, s = dict_and_set()
    d[9] = 9
    s.add(9)
    print(len(d), len(s), d[9])
    p, q = two_recs()
    p.v = 5
    print(p.v, q.v)


main()
