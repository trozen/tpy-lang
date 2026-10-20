# A @dataclass field whose type holds a reference type -- a tuple of records,
# flat or nested -- takes ownership in the generated __init__ like a record
# field: a last-use or fresh element moves in, a live one warns, copy() opts
# out. A Ptr field stays a plain parameter and aliases.
from dataclasses import dataclass
from tpy import Own, Ptr, copy, int32, nocopy


@dataclass
class P:
    n: int32


@dataclass
class H:
    pair: tuple[P, P]


@dataclass
class D:
    v: tuple[tuple[P, int32], int32]

    # Written out: the generated == rejects a nested tuple of records
    # (BUGS.md#nested-record-tuple-field-compare-read-rejects); the generated
    # __init__ is the subject.
    def __eq__(self, other: "D") -> bool:
        return True

    def __repr__(self) -> str:
        return "D"


@dataclass
class V:
    t: tuple[int32, str]


@nocopy
class K:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __eq__(self, other: "K") -> bool:
        return self.n == other.n

    def __repr__(self) -> str:
        return f"K({self.n})"


@dataclass
class KH:
    pair: tuple[K, int32]


@dataclass
class R:
    p: Ptr[P]

    # Written out: the generated repr does not take a Ptr field
    # (BUGS.md#fstring-optional-ptr-rejects).
    def __eq__(self, other: "R") -> bool:
        return True

    def __repr__(self) -> str:
        return "R"


@dataclass
class Q:
    n: int32


@dataclass
class G[T]:
    pair: tuple[T, int32]

    # Written out: a generic dataclass's generated methods reject
    # (BUGS.md#generic-dataclass-rejects).
    def __eq__(self, other: "G[T]") -> bool:
        return True

    def __repr__(self) -> str:
        return "G"


@dataclass
class O:
    pair: tuple[P | None, int32]


@dataclass
class UQ:
    pair: tuple[P | Q, int32]

    # Written out: the generated == rejects a union-of-records leaf
    # (BUGS.md#nested-record-tuple-field-compare-read-rejects).
    def __eq__(self, other: "UQ") -> bool:
        return True

    def __repr__(self) -> str:
        return "UQ"


def wrap(u: P | Q) -> None:
    # a borrowed union parameter is the caller's storage: the element copies, warned
    h = UQ((u, 2))  # tpyc: warning(/copies P \| Q into owned storage \(argument 'pair' tuple element 0\)/)
    print("union leaf:", h)


def make() -> tuple[Own[P], Own[P]]:
    return (P(5), P(6))


def main() -> None:
    # copy() opt-out: the field holds its own P, the original's later write
    # does not reach it
    p = P(1)
    q = P(2)
    h = H((copy(p), q))
    p.n = 9
    print("copy:", h.pair[0].n, p.n)

    # last-use literal elements move in
    a = P(3)
    b = P(4)
    h2 = H((a, b))
    h2.pair[0].n = 30
    print("last-use literal:", h2.pair[0].n, h2.pair[1].n)

    # last-use tuple name moves in
    t = (P(7), P(8))
    h3 = H(t)
    h3.pair[1].n = 80
    print("last-use name:", h3.pair[0].n, h3.pair[1].n)

    # call result moves in
    h4 = H(make())
    h4.pair[0].n = 50
    print("call result:", h4.pair[0].n)

    # keyword argument, last-use element
    c = P(10)
    h5 = H(pair=(c, P(11)))
    h5.pair[0].n = 100
    print("keyword:", h5.pair[0].n)

    # nested field: Own of the whole nested tuple
    d = D(((P(12), 1), 2))
    d.v[0][0].n = 120
    inner = d.v[0]  # bound first: BUGS.md#nested-record-tuple-field-compare-read-rejects
    print("nested:", inner[0].n, inner[1])

    # @nocopy element compiles only because the last use moves it
    k = K(13)
    kh = KH((k, 3))
    kh.pair[0].n = 130
    print("nocopy:", kh.pair[0].n, kh.pair[1])

    # value-only tuple: a plain parameter, nothing to own
    v = V((1, "x"))
    print("value tuple:", v.t[0], v.t[1])

    # live element: the copy into owned storage warns, like a record field
    live = P(20)
    h6 = H((live, P(21)))  # tpyc: warning(/copies P into owned storage \(argument 'pair' tuple element 0\)/)
    print("live:", h6.pair[1].n, live.n)

    # live tuple name: `lt` is read afterwards, so its elements copy, warned
    lt = (P(22), P(23))
    h7 = H(lt)  # tpyc: warning(/copies P into owned storage \(tuple element 0\)/) warning(/copies P into owned storage \(tuple element 1\)/)
    print("live name:", h7.pair[0].n, lt[1].n)

    # generic field at a record: Own[tuple[T, int32]], a live element warns
    gp = P(25)
    g = G[P]((gp, 1))  # tpyc: warning(/copies P into owned storage \(argument 'pair' tuple element 0\)/)
    print("generic record:", g.pair[0].n, gp.n)
    # generic field at a value type: nothing to copy
    gv = G[int32]((3, 4))  # tpyc: ok
    print("generic value:", gv.pair[0], gv.pair[1])

    # Optional leaf: the record payload is owned like a record element
    op = P(26)
    o = O((op, 5))  # tpyc: warning(/copies P into owned storage \(argument 'pair' tuple element 0\)/)
    oq = o.pair[0]
    if oq is not None:
        print("optional leaf:", oq.n, op.n)

    # union-of-records leaf, from a borrowed parameter
    wrap(Q(27))

    # Ptr field: no ownership taken, the field aliases the original
    target = P(24)
    r = R(target)  # tpyc: ok
    r.p.n = 240
    print("ptr:", target.n)


main()
