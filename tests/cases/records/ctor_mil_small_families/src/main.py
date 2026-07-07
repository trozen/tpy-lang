# Ctor member-init-list small value families: None into pointer-repr / value
# Optional, Ptr and union fields; value- and pointer-variant union sources;
# tuple param and literal sources; and a bare-global-name init the compiler
# demotes to the ctor body (chain-breaking every later init). Union and tuple
# fields store copies by design (value-variant / storage-form semantics).
from tpy import Int32, Float64, Ptr


class A:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x


class B:
    y: Int32

    def __init__(self, y: Int32) -> None:
        self.y = y


G: Int32 = 9


class H:
    items: list[Int32] | None
    ox: Int32 | None
    vu: Int32 | Float64
    un: Int32 | Float64 | None
    pu: A | B
    pr: A | B
    pn: A | B | None
    ft: tuple[A, Int32]
    vt: tuple[Int32, Int32]
    tl: tuple[Int32, Int32]

    def __init__(self, pu: A | B, ft: tuple[A, Int32],
                 vt: tuple[Int32, Int32]) -> None:
        self.items = None
        self.ox = None
        self.vu = 5
        self.un = None
        self.pu = pu  # tpyc: warning(/copies A \| B into field/)
        self.pr = A(3)
        self.pn = None
        self.ft = ft  # tpyc: warning(/copies A into field \(tuple element 0\)/)
        self.vt = vt
        self.tl = (1, 2)


class P:
    p: Ptr[Int32]

    def __init__(self) -> None:
        self.p = None


class D:
    n: Int32
    strict: bool

    def __init__(self) -> None:
        self.n = G  # bare global name: demoted to the ctor body
        self.strict = True


def main() -> None:
    a = A(1)
    h = H(a, (a, 2), (3, 4))
    print(h.items is None, h.ox is None)
    print(h.vu)
    print(h.un is None)
    pu = h.pu
    print(isinstance(pu, A))
    pr = h.pr
    print(isinstance(pr, A))
    print(h.pn is None)
    print(h.ft[1], h.vt[0], h.tl[1])
    q = P()
    print(q.p is None)
    d = D()
    print(d.n, d.strict)


main()
