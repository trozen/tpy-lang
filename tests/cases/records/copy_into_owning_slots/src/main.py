# A `copy()` stored into a field (member-init and a later write) and returned
# at `Own[P]` owns a copy: a later write to the source shows in none. A
# field of a fresh call result (`mk(v).p`) moves out of the temporary at
# every owning position, with no copy and no warning.
from tpy import Own, copy, int32


class P:
    def __init__(self, v: int32) -> None:
        self.v = v


class W:
    p: P

    def __init__(self, p: Own[P]) -> None:
        self.p = p


def mk(v: int32) -> Own[W]:
    return W(P(v))


class H:
    p: P

    # member_init: the copy the record declares EXPLICIT
    def __init__(self, p: P) -> None:
        self.p = copy(p)  # tpyc: ok

    # field: the copy the record declares EXPLICIT
    def reset(self, p: P) -> None:
        self.p = copy(p)  # tpyc: ok

    # field_fresh: the temporary's field moves in
    def refill(self, v: int32) -> None:
        self.p = mk(v).p  # tpyc: ok


class G:
    p: P

    # member_init_fresh: the temporary's field moves in
    def __init__(self, v: int32) -> None:
        self.p = mk(v).p  # tpyc: ok


# return: the copy moves into the owned result
def ret(p: P) -> Own[P]:
    return copy(p)  # tpyc: ok


# return_fresh: the temporary's field moves into the owned result
def ret_fresh(v: int32) -> Own[P]:
    return mk(v).p  # tpyc: ok


# local_fresh: the temporary's field moves into the local, then out
def local_fresh(v: int32) -> Own[P]:
    x = mk(v).p  # tpyc: ok
    return x


def main() -> None:
    p = P(1)
    h = H(p)
    p.v = 2
    print("member_init", h.p.v, p.v)
    h.reset(p)
    p.v = 3
    print("field", h.p.v, p.v)
    q = ret(p)
    p.v = 4
    print("return", q.v, p.v)
    h.refill(5)
    print("field_fresh", h.p.v)
    print("member_init_fresh", G(6).p.v)
    print("return_fresh", ret_fresh(7).v)
    print("local_fresh", local_fresh(8).v)


main()
