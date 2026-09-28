# A tuple-unpack source whose call argument needs a hoisted temp (a record
# rvalue, a union-lifted literal) flushes the temp before the holder line.
import asyncio
from typing import Iterator
from tpy import Own, error_return, ReturnException


class M:
    v: int

    def __init__(self) -> None:
        self.v = 3


class E(Exception, ReturnException):
    pass


class Cm:
    def __init__(self, m: Own[M]) -> None:
        self.m = m

    def __enter__(self) -> int:
        return self.m.v

    def __exit__(self, et, ev, tb) -> None:
        pass


def f(m: M) -> tuple[int, int]:
    return m.v, m.v + 1


def send(d: bytes | dict[str, str] | None) -> tuple[int, int]:
    if isinstance(d, bytes):
        return len(d), 1
    return 0, 0


def sp(m: M) -> tuple[str, int]:
    return "s" + str(m.v), m.v


def gv[T](m: M, x: T) -> tuple[T, int]:
    return x, m.v


def pick(m: M) -> tuple[M, M, int]:
    return m, m, m.v


@error_return(E)
def ef(m: M) -> tuple[int, int]:
    if m.v < 0:
        raise E()
    return m.v, 2


class K:
    s: int

    def __init__(self) -> None:
        # constructor body
        a, b = f(M())  # tpyc: ok
        self.s = a + b

    def run(self) -> int:
        # method body
        a, b = f(M())  # tpyc: ok
        return a + b + self.s


def gen() -> Iterator[int]:
    # generator: value targets land in frame fields
    a, b = f(M())  # tpyc: ok
    yield a
    c, d = send(b"hi")  # tpyc: ok
    yield b + c + d


async def co() -> int:
    # async: value targets survive the await
    a, b = f(M())  # tpyc: ok
    await asyncio.sleep(0)
    c, d = send(b"hey")  # tpyc: ok
    await asyncio.sleep(0)
    return a + b + c + d


@error_return(E)
def er_body() -> int:
    # @error_return body, plain source and @error_return call source
    a, b = f(M())  # tpyc: ok
    c, d = ef(M())  # tpyc: ok
    return a + b + c + d


def main() -> None:
    # free function
    a, b = f(M())  # tpyc: ok
    print("free", a, b)

    # union-lift arg
    c, d = send(b"hi")  # tpyc: ok
    print("union_lift", c, d)

    print("method_ctor", K().run())

    for x in gen():
        print("generator", x)

    print("async", asyncio.run(co()))

    # closure
    def inner() -> int:
        p, q = f(M())  # tpyc: ok
        return p + q
    print("closure", inner())

    # try/finally body
    try:
        e, g = f(M())  # tpyc: ok
        print("try", e, g)
    finally:
        print("finally")

    # with body
    with Cm(M()) as w:
        h, i = f(M())  # tpyc: ok
        print("with", w, h, i)

    # match arm
    k = 1
    match k:
        case 1:
            j, l = f(M())  # tpyc: ok
            print("match", j, l)
        case _:
            pass

    # loop body: the temp is rebuilt per iteration
    for it in range(2):
        m1, m2 = f(M())  # tpyc: ok
        print("loop", it, m1, m2)

    try:
        print("error_return", er_body())
    except E:
        print("error_return E")

    # generic twin
    g1, g2 = gv(M(), 7)  # tpyc: ok
    print("generic", g1, g2)

    # str element
    s, n = sp(M())  # tpyc: ok
    print("str_elem", s, n)

    # sync alias: both targets alias the one temp, which lives to block end
    r1, r2, rn = pick(M())  # tpyc: ok
    r1.v = 9
    print("alias", r2.v, rn)


main()

# module level
ma, mb = f(M())  # tpyc: ok
print("module", ma, mb)
