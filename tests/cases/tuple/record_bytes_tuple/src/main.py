# A tuple mixing a borrowed record with an owned `bytes` element compiles at
# every position the `str` twin does, and the record element aliases the
# caller's object. Container inserts copy the record (warned, as for `str`).
import asyncio
from typing import Iterator
from tpy import int32, Own, String


class Rec:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


# free function: a borrowed record and an owned bytes element returned
def pair(p: Rec) -> tuple[Rec, bytes]:
    return (p, b"m")  # tpyc: ok


# mixed borrow + Own + bytes return
def mix(p: Rec) -> tuple[Rec, Own[Rec], bytes]:
    return (p, Rec(2), b"x")  # tpyc: ok


def show(t: tuple[Rec, bytes]) -> None:
    t[0].n += 100  # tpyc: ok
    print("param", t[0].n, t[1])


# a mixed tuple param that only reads its Own element never consumes it
def mixp(t: tuple[Rec, Own[Rec], bytes]) -> None:  # tpyc: warning(/never consumed/)
    print("mixparam", t[0].n, t[1].n, t[2])


# String is the other owned member of the view family
def spair(p: Rec) -> tuple[Rec, String]:
    return (p, String("s"))  # tpyc: ok


# String param: the record element aliases the caller's object
def sshow(t: tuple[Rec, String]) -> None:
    t[0].n += 100  # tpyc: ok
    print("string param", t[0].n, t[1])


class Holder:
    def __init__(self) -> None:
        pass

    # method
    def get(self, p: Rec) -> tuple[Rec, bytes]:
        return (p, b"h")  # tpyc: ok


# generator
def gen(p: Rec) -> Iterator[tuple[Rec, bytes]]:
    yield (p, b"g")  # tpyc: ok


# async body
async def amain(r: Rec) -> None:
    await asyncio.sleep(0)
    t = pair(r)  # tpyc: ok
    t[0].n = 11
    print("async", r.n, t[1])


def main() -> None:
    r = Rec(1)
    # local bound from the call, mutated through the element
    t = pair(r)  # tpyc: ok
    t[0].n = 2
    print("local", r.n, t[1])
    show(t)
    print("param_after", r.n)
    # call-source unpack
    a, s = pair(r)  # tpyc: ok
    a.n = 3
    print("unpack", r.n, s)
    # element bound to a local: an owned copy
    y = t[1]  # tpyc: ok
    print("subscript", y)
    # tuple literal local
    lt: tuple[Rec, bytes] = (r, b"l")  # tpyc: ok
    lt[0].n = 5
    print("literal", r.n, lt[1])
    # mixed Own call-source unpack
    b, o, s3 = mix(r)  # tpyc: ok
    b.n = 6
    o.n = 7
    print("mix", r.n, o.n, s3)
    mixp(mix(r))
    # method
    m = Holder().get(r)  # tpyc: ok
    m[0].n = 9
    print("method", r.n, m[1])
    # generator
    for g in gen(r):  # tpyc: ok
        g[0].n = 10
        print("gen", r.n, g[1])
    asyncio.run(amain(r))

    # closure body
    def inner() -> None:
        c = pair(r)  # tpyc: ok
        c[0].n = 12
        print("closure", r.n, c[1])
    inner()
    # container element: the insert copies the record
    xs: list[tuple[Rec, bytes]] = []
    xs.append(pair(r))  # tpyc: warning(/copies Rec into owned storage/)
    for e0, e1 in xs:  # tpyc: ok
        print("container", e0.n, e1)
    ys: list[tuple[Rec, Rec, bytes]] = []
    ys.append(mix(r))  # tpyc: warning(/copies Rec into owned storage/)
    for w in ys:
        print("mixstore", w[1].n, w[2])
    # try / finally
    u = pair(r)
    try:
        u[0].n = 21  # tpyc: ok
    finally:
        print("finally", r.n, u[1])
    # match arm
    k = 1
    match k:
        case 1:
            u[0].n = 22  # tpyc: ok
            print("match", r.n, u[1])
        case _:
            pass
    # String element
    st = spair(r)  # tpyc: ok
    st[0].n = 23
    print("string", r.n, st[1])
    sshow(st)
    print("string param_after", r.n)
    # String call-source unpack
    sa, ss = spair(r)  # tpyc: ok
    sa.n = 24
    print("string unpack", r.n, ss)
    # String container element: the insert copies the record
    zs: list[tuple[Rec, String]] = []
    zs.append(spair(r))  # tpyc: warning(/copies Rec into owned storage/)
    for z0, z1 in zs:  # tpyc: ok
        print("string container", z0.n, z1)


main()
