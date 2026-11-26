# Shared scaffolding: fresh objects whose storage a statement reads after
# building them, callees that lend an argument, and `*args` sinks.
from tpy import int32, Own, StrView, nomove


class P:
    n: int32
    tag: str

    def __init__(self, n: int32) -> None:
        self.n = n
        self.tag = "p" * 40


class M:
    ps: list[P]
    p: P
    name: str

    def __init__(self, n: int32) -> None:
        self.ps = [P(n), P(n + 1)]
        self.p = P(n)
        self.name = "m" * 40


@nomove
class NM:
    v: int32

    def __init__(self, v: int32) -> None:
        print("mk NM", v)
        self.v = v


def mk(tag: str, n: int32) -> Own[M]:
    print("mk", tag)
    return M(n)


def side(tag: str, n: int32) -> Own[P]:
    print("side", tag)
    return P(n)


def first(xs: list[P]) -> P:
    return xs[0]


def same(m: M) -> M:
    return m


def head(s: str) -> StrView:
    return s[0:35]


def show(label: str, *xs: StrView) -> None:
    for x in xs:
        print(label, x, len(x))


def total(*xs: StrView) -> int32:
    t = 0
    for x in xs:
        t += len(x)
    return t


def tick(*xs: int) -> int:
    t: int = 0
    for x in xs:
        t += x
    return t


def addall(*ls: list[int32]) -> int32:
    for l in ls:
        l.append(len(l))
    return len(ls)


def sum_nm(*args: NM) -> int32:
    s = 0
    for a in args:
        s += a.v
    return s
