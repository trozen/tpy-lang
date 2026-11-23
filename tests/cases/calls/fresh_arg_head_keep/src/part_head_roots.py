# What a head keeps is the fresh root: never a copy of a borrow out of it, nor
# a receiver a consuming method takes; names bound past the statement live on.
from typing import Iterator, Self
from tpy import Own, int32, nocopy


@nocopy
class Inner:
    d: dict[str, int32]

    def __init__(self, n: int32) -> None:
        self.d = {}
        for k in range(2):
            self.d["key-long-enough-to-heap-" + str(n * 10 + k)] = n

    def items(self) -> dict[str, int32]:
        return self.d


class Outer:
    inner: Inner

    def __init__(self, n: int32) -> None:
        self.inner = Inner(n)

    def get(self) -> Inner:
        return self.inner


class Bin:
    xs: list[int32]

    def __init__(self, n: int32) -> None:
        self.xs = [n, n + 1]

    def take(self: Own[Self]) -> Own[list[int32]]:
        return self.xs


class Src:
    xs: list[int32]

    def __init__(self, n: int32) -> None:
        self.xs = [n, n + 1]


class CS:
    n: int32

    def __init__(self, s: Src) -> None:
        self.n = s.xs[0]

    def __enter__(self) -> str:
        return "a-long-string-that-defeats-sso-" + str(self.n) * 20

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


class Mgr:
    log: list[str]

    def __init__(self) -> None:
        self.log = []

    def __enter__(self) -> list[str]:
        self.log.append("enter-long-enough-string-to-heap")
        return self.log

    def __exit__(self, a, b, c) -> None:
        self.log.append("exit")


class Box:
    m: Mgr

    def __init__(self) -> None:
        self.m = Mgr()

    def guard_m(self) -> Mgr:
        return self.m


class Pin:
    xs: list[int32]

    def __init__(self, xs: list[int32]) -> None:
        self.xs = [xs[0], xs[1]]

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


def make(n: int32) -> Own[Outer]:
    return Outer(n)


def mkb() -> Own[Box]:
    return Box()


def ident[T](x: T) -> T:
    return x


def walk(s: Src) -> list[int32]:
    return s.xs


def rep[T](obj: T, n: int32) -> Iterator[T]:
    for _ in range(n):
        yield obj


def rep_s(obj: str, n: int32) -> Iterator[str]:
    for _ in range(n):
        yield obj


def mks() -> str:
    return "ab" * 20


def roots() -> None:
    # a field of a fresh value, a borrow out of one (a @nocopy record)
    out: list[str] = []
    for k in make(1).inner.items():  # tpyc: ok
        out.append(k)
    for k in make(2).get().items():  # tpyc: ok
        out.append(k)
    print("roots borrowed", out)
    # a consuming method's receiver is handed over, not kept
    for x in Bin(3).take():  # tpyc: ok
        print("roots consuming", x)
    # an open `T` callee hands its argument back
    for x in walk(ident(Src(4))):  # tpyc: ok
        print("roots open", x)
    # a generic generator at `str` owns its copy; the monomorphic twin views
    g = 0
    for s in rep(mks(), 2):  # tpyc: ok
        g += len(s)
    for s in rep_s(mks(), 2):  # tpyc: ok
        g += len(s)
    print("roots generic", g)


def bound_after() -> None:
    # a `str` target read after the statement owns its buffer
    with CS(Src(2)) as b:  # tpyc: ok
        pass
    junk = ["x" * 60 for _ in range(4)]
    print("bound str", b[0:12], len(junk))
    # a borrowed manager's receiver lives as long as the target
    with mkb().guard_m() as q:  # tpyc: ok
        q.append("body")
    q.append("after")
    r = q
    r.append("via-r")
    print("bound borrowed", len(q), q[0][0:5], q[-2], q[-1])


def gen_roots(k: int32) -> Iterator[str]:
    # in a generator: a borrow out of a fresh value keeps the value on the frame
    for key in make(k).get().items():  # tpyc: ok
        junk = ["y" * 40 for _ in range(4)]
        yield key[-2:] + str(len(junk))
    # a borrowed manager's receiver, the target read across a yield
    with mkb().guard_m() as q:  # tpyc: ok
        q.append("gen-body")
    # the manager's `__exit__` appended to what `q` refers to
    yield q[0][0:5] + "/" + q[-1]
    r = q
    r.append("gen-via-r")
    yield q[-1]


def gen(k: int32) -> Iterator[int32]:
    # a comprehension inside a frame head keeps its own source in its block
    with Pin([x * 2 for x in walk(Src(k))]) as c:  # tpyc: ok
        yield c.xs[0]
        yield c.xs[1]


def main() -> None:
    roots()
    bound_after()
    print("roots comp", list(gen(1)))
    print("roots gen", list(gen_roots(5)))
