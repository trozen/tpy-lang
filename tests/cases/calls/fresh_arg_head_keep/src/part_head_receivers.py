# A fresh receiver of a method or a @property read in a compound head is
# kept until the statement ends; a `with` target read after it stays valid.
from tpy import int32, Own, Ptr, StrView

G: list[int32] = [1, 2]


class Vec:
    xs: list[int32]

    def __init__(self, n: int32, step: int32 = 1) -> None:
        self.xs = [n, n + step]

    def __add__(self, o: "Vec") -> Own["Vec"]:
        v = Vec(0)
        v.xs = [self.xs[0], o.xs[0]]
        return v

    def items(self) -> list[int32]:
        return self.xs

    @property
    def view(self) -> list[int32]:
        return self.xs


class Guard:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def __enter__(self) -> int32:
        self.n += 1
        return self.n

    def __exit__(self, kind, value, tb) -> None:
        pass


GUARD: Guard = Guard()


class H:
    s: str

    def __init__(self) -> None:
        self.s = "abcdef"

    # storage that outlives the receiver: read live, never a snapshot
    @property
    def items(self) -> list[int32]:
        return G

    @property
    def guard(self) -> Guard:
        return GUARD

    def guard_m(self) -> Guard:
        return GUARD

    @property
    def head(self) -> StrView:
        return self.s[0:3]


class Src:
    xs: list[int32]

    def __init__(self, n: int32) -> None:
        self.xs = [n, n + 1]


class CM:
    s: Ptr[Src]

    def __init__(self, s: Src) -> None:
        self.s = s

    def __enter__(self) -> int32:
        return len(self.s.xs)

    def __exit__(self, kind, value, tb) -> None:
        pass


def side(n: int32) -> int32:
    return n * 10


def pair(a: int32, xs: list[int32]) -> int32:
    return a + len(xs)


class Gate:
    n: int32

    def __init__(self, s: Src) -> None:
        self.n = s.xs[0]

    def __enter__(self) -> "Gate":
        return self

    def __exit__(self, kind, value, tb) -> None:
        pass


def mkv(n: int32) -> Own[Vec]:
    return Vec(n)


def mk() -> Own[H]:
    return H()


def receivers() -> None:
    # free-call, operator and keyword-constructed receivers at a for head
    for x in mkv(7).items():  # tpyc: ok
        print("recv free", x)
    for x in (Vec(1) + Vec(10)).items():  # tpyc: ok
        print("recv binop", x)
    for x in Vec(5, step=3).items():  # tpyc: ok
        print("recv kwargs", x)
    for x in mkv(9).view:  # tpyc: ok
        print("recv property", x)


def properties() -> None:
    # a property off a fresh receiver: for, comprehension, with, match heads
    n = 0
    for r in mk().items:  # tpyc: ok
        n += r
    print("prop for", n)
    print("prop comps", [x + 1 for x in mk().items], len({x: x for x in mk().items}),  # tpyc: ok
          len({x for x in mk().items}))  # tpyc: ok
    with mk().guard as g:  # tpyc: ok
        print("prop with", g)
    # the method spelling of the same manager
    with mk().guard_m() as g2:  # tpyc: ok
        print("method with", g2)
    match mk().head:  # tpyc: ok
        case "abc":
            print("prop match abc")
        case _:
            print("prop match other")
    # the properties hand back the globals' own storage: a write after the
    # statement shows through the next read, never a snapshot
    G.append(7)
    live = [x for x in mk().items]  # tpyc: ok
    print("prop live", live[-1])
    G.pop()
    GUARD.n += 10
    with mk().guard as g3:  # tpyc: ok
        print("prop guard live", g3)


def with_after() -> None:
    # a value target read after the statement whose head keeps a fresh
    # argument: declared before the statement's scope
    with CM(Src(5)) as n:  # tpyc: ok
        print("with in", n)
    print("with after", n)
    # a reference target read after it: the statement keeps the enclosing
    # scope, and with it the manager the target refers to
    with Gate(Src(8)) as g:  # tpyc: ok
        print("with ref in", g.n)
    print("with ref after", g.n)


def order() -> None:
    # two fresh arguments of one call evaluate in C++'s order (a deliberate
    # divergence): no side effect is printed, the render is pinned
    print("order", pair(side(5), [x for x in Vec(6).items()]))  # tpyc: ok


class PCM:
    xs: list[int32]

    def __init__(self, n: int32) -> None:
        self.xs = [n, n + 1]

    @property
    def items(self) -> list[int32]:
        return self.xs

    def __enter__(self) -> "PCM":
        return self

    def __exit__(self, kind, value, tb) -> None:
        pass


class PInner:
    cm: PCM
    s: str

    def __init__(self, n: int32) -> None:
        self.cm = PCM(n)
        self.s = "ab" + str(n)

    @property
    def get_cm(self) -> PCM:
        return self.cm

    @property
    def tag(self) -> StrView:
        return self.s[0:2]


class POuter:
    inner: PInner

    def __init__(self, n: int32) -> None:
        self.inner = PInner(n)

    @property
    def get_inner(self) -> PInner:
        return self.inner


def chained() -> None:
    # a chain of properties off a fresh receiver: the head keeps the root
    for x in POuter(1).get_inner.get_cm.items:  # tpyc: ok
        print("chain for", x)
    with POuter(2).get_inner.get_cm as c:  # tpyc: ok
        c.xs.append(9)
        print("chain with", c.xs[-1])
    match POuter(3).get_inner.tag:  # tpyc: ok
        case "ab":
            print("chain match ab")
        case _:
            print("chain match other")


def main() -> None:
    receivers()
    chained()
    properties()
    with_after()
    order()
