# Heads unpacking a step that hands its members over: every write, move or
# consumption of a target reaches the member the step holds, for and
# comprehension heads alike; a read-only head is the neighbour. The `int`
# member makes the holder worth a const reference.
from typing import Iterator
from tpy import Fn, Own, int32, own_iter


class B:
    def __init__(self, v: int32) -> None:
        self.v = v

    def bump(self) -> None:
        self.v += 1

    def bumpv(self) -> int32:
        self.v += 1
        return 0


def g(n: int) -> Iterator[tuple[int, Own[B]]]:
    for i in range(n):
        yield (i, B(int32(i) * 10))


def run0(f: Fn[[], int32]) -> int32:
    f()
    return 0


def lam_then(f: Fn[[], int32], b: B) -> int32:
    f()
    return b.v


def bumped(b: B) -> int32:
    b.bump()
    return b.v


def writes() -> None:
    t = 0
    for i, b in g(2):
        b.v += 1  # tpyc: ok
        t += b.v
    print("ho_aug", t)
    t = 0
    for i, b in g(2):
        b.bump()  # tpyc: ok
        t += b.v
    print("ho_mcall", t)
    t = 0
    for i, b in g(2):
        later(b)  # tpyc: ok
        t += b.v
    print("ho_late", t)
    t = 0
    for i, b in g(2):
        run0(lambda: b.bumpv())  # tpyc: ok
        t += b.v
    print("ho_lam", t)
    t = 0
    for i, b in g(2):
        if (y := b).v >= 0:  # tpyc: ok
            y.bump()
        t += b.v
    print("ho_walrus", t)
    t = 0
    for i, b in g(2):
        z = b  # tpyc: ok
        z.bump()
        t += b.v
    print("ho_alias", t)
    t = 0
    for i, b in g(2):
        match b:
            case B() as c:
                c.bump()  # tpyc: ok
        t += b.v
    print("ho_match", t)
    t = 0
    for i, b in g(2):
        def h() -> None:
            b.bump()  # tpyc: ok
        h()
        t += b.v
    print("ho_nested", t)
    # Comprehension heads: a write through a call and through a lambda.
    print("ho_comp_mcall", [bumped(b) for i, b in g(2)])  # tpyc: ok
    print("ho_comp_lam", [lam_then(lambda: b.bumpv(), b) for i, b in g(2)])  # tpyc: ok
    # Neighbour: a read-only head.
    t = 0
    for i, b in g(2):
        t += b.v + int32(i)  # tpyc: ok
    print("ho_ro", t, [b.v + int32(i) for i, b in g(2)])


def later(b: B) -> None:
    b.v += 100


def rows() -> Iterator[tuple[Own[list[int32]], int]]:
    yield ([3, 4], 1)
    yield ([5], 2)


def consumed() -> None:
    # Explicit consumption of a handed-over target, for and comprehension.
    out: list[int32] = []
    for row, i in rows():
        for n in own_iter(row):  # tpyc: ok
            out.append(n)
    print("ho_own_iter", out, [n for row, i in rows() for n in own_iter(row)])  # tpyc: ok
    # Neighbour: the target only read.
    print("ho_own_read", [len(row) for row, i in rows()])


def owned_rows() -> Iterator[Own[list[int32]]]:
    yield [3, 4]
    yield [5]


def clause_shadow(xs: list[int32]) -> None:
    # The clause target hides the borrowed parameter: own_iter consumes the
    # target, which owns its row; the parameter is untouched after.
    print("ho_clause_shadow", [n for xs in owned_rows() for n in own_iter(xs)], xs)  # tpyc: ok
    # Neighbour: the renamed twin.
    print("ho_clause_twin", [n for row in owned_rows() for n in own_iter(row)], xs)
    xs.append(11)
    print("ho_clause_after", xs)


class UA:
    def __init__(self, n: int32) -> None:
        self.n = n


class UB:
    def __init__(self, n: int32) -> None:
        self.n = n


def unions() -> Iterator[tuple[Own[UA | UB], int]]:
    yield (UA(13), 1)
    yield (UB(14), 2)


def value(x: UA | UB) -> int32:
    if isinstance(x, UA):
        return x.n
    return x.n


def union_reads() -> None:
    # An owned union member read through a handed-over target.
    for a, i in unions():
        print("ho_union", value(a), i)  # tpyc: ok
    # Neighbour: a test of the member. (A comprehension head over this step
    # is refused today: BUGS.md#comp-owned-union-step-rejects.)
    for a, i in unions():
        print("ho_union_isinst", isinstance(a, UA), i)


def main() -> None:
    writes()
    consumed()
    clause_shadow([10])
    union_reads()
