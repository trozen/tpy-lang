# A local bound from a call that lends a pointer-form result (`-> Optional[R]`,
# `-> readonly[Optional[R]]`, `-> R | S`) aliases the lent object at every
# position; an owning sink copies it under the copy warning and never moves it.
from typing import Optional, Iterator
import asyncio
from tpy import int32, readonly, Own, error_return, ReturnException


class NotFound(Exception, ReturnException):
    pass


class R:
    xs: list[int32]

    def __init__(self) -> None:
        self.xs = [1, 2, 3]


class S:
    y: int32

    def __init__(self, y: int32) -> None:
        self.y = y


class H:
    o: Optional[R]

    def __init__(self) -> None:
        self.o = R()

    def geto(self) -> Optional[R]:
        return self.o

    @readonly
    def get(self) -> readonly[Optional[R]]:
        return self.o

    @property
    def p(self) -> Optional[R]:
        return self.o


class K:
    r: R
    o: Optional[R]

    def __init__(self) -> None:
        self.r = R()
        self.o = None


class C:
    r: R

    def __init__(self, h: H) -> None:
        self.r = R()
        q = h.geto()
        if q is not None:
            self.r = q  # tpyc: warning(/copies R into field/)


class Cm:
    def __enter__(self) -> int32:
        return 0

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


def getm(h: H) -> Optional[R]:
    return h.o


def first[T](xs: list[T]) -> Optional[T]:
    if len(xs) > 0:
        return xs[0]
    return None


def pick(a: R, b: S, c: bool) -> R | S:
    if c:
        return a
    return b


def take(r: Own[R]) -> Own[R]:
    return r


def mko() -> Own[Optional[R]]:
    return R()


def mkr() -> Own[R]:
    return R()


def olen(h: H) -> int32:
    o = h.o
    return len(o.xs) if o is not None else -1


# dict.get spelling: the rebind aliases the dict entry
def s_dict_get() -> None:
    d: dict[str, R] = {"a": R()}
    q = d.get("a")
    if q is not None:
        r = q
        r.xs.append(4)
    print("dict_get", len(d["a"].xs))


# method spelling
def s_method(h: H) -> None:
    q = h.geto()
    if q is not None:
        r = q
        r.xs.append(4)
    print("method", olen(h))


# free-function spelling
def s_free(h: H) -> None:
    q = getm(h)
    if q is not None:
        r = q
        r.xs.append(5)
    print("free", olen(h))


# generic call at a reference type
def s_generic(xs: list[R]) -> None:
    q = first(xs)
    if q is not None:
        r = q
        r.xs.append(6)
    print("generic", len(xs[0].xs))


# pointer-variant union result
def s_union(a: R, b: S) -> None:
    u = pick(a, b, True)
    if isinstance(u, R):
        v = u
        v.xs.append(7)
    print("union", len(a.xs))


# readonly face: the rebind sees a later write to the source
def s_readonly(h: H) -> None:
    q = h.get()
    if q is not None:
        r = q
        if h.o is not None:
            h.o.xs.append(9)
        print("readonly", len(r.xs))


# walrus
def s_walrus(h: H) -> None:
    if (q := h.geto()) is not None:
        r = q
        r.xs.append(8)
    print("walrus", olen(h))


# assert narrowing
def s_assert(h: H) -> None:
    q = h.geto()
    assert q is not None
    r = q
    r.xs.append(8)
    print("assert", olen(h))


# closure capture
def s_closure(h: H) -> None:
    q = h.geto()
    if q is not None:
        r = q

        def bump() -> None:
            r.xs.append(1)
        bump()
    print("closure", olen(h))


# try body (hoisted binding)
def s_try(h: H) -> None:
    q = h.geto()
    try:
        if q is not None:
            r = q
            r.xs.append(2)
    finally:
        print("try", olen(h))


# with body (hoisted binding)
def s_with(h: H) -> None:
    q = h.geto()
    with Cm():
        if q is not None:
            r = q
            r.xs.append(3)
    print("with", olen(h))


# @error_return body
@error_return(NotFound)
def s_error_return(h: H) -> int32:
    q = h.geto()
    if q is None:
        raise NotFound
    r = q
    r.xs.append(4)
    return olen(h)


# tuple local: the element aliases
def s_tuple(h: H) -> None:
    q = h.geto()
    if q is not None:
        t = (q, 1)
        e = t[0]
        e.xs.append(5)
    print("tuple", olen(h))


# ternary over a lending call: the narrowed result aliases
def s_ternary_alias(xs: list[R], c: bool) -> None:
    y = first(xs) if c else None
    if y is not None:
        r = y
        r.xs.append(6)
    print("ternary_alias", len(xs[0].xs))


# ternary over a lending call into an owning sink: copied, source intact
def s_ternary_sink(xs: list[R], c: bool) -> None:
    ys: list[R] = []
    y = first(xs) if c else None
    if y is not None:
        ys.append(y)  # tpyc: warning(/copies R into owned storage/)
    print("ternary_sink", len(ys), len(xs[0].xs))


# owning sinks copy (warned) and leave the source intact
def s_append(h: H) -> None:
    ys: list[R] = []
    q = h.geto()
    if q is not None:
        ys.append(q)  # tpyc: warning(/copies R into owned storage/)
    print("append", len(ys), olen(h))


def s_own_arg(h: H) -> None:
    q = h.get()
    if q is not None:
        t = take(q)  # tpyc: warning(/copies R into owned storage/)
        print("own_arg", len(t.xs), olen(h))


def s_own_ret(h: H) -> Own[R]:
    q = h.geto()
    if q is not None:
        return q  # tpyc: warning(/copies R into owned storage/)
    return R()


def s_field(h: H, k: K) -> None:
    q = h.geto()
    if q is not None:
        k.r = q  # tpyc: warning(/copies R into field/)
    print("field", olen(h))


def s_comp(h: H) -> None:
    q = h.geto()
    if q is not None:
        ys = [q for _ in range(2)]  # tpyc: warning(/copies R into owned storage/)
        print("comp", len(ys), olen(h))


def s_opt_field(h: H, k: K) -> None:
    q = h.geto()
    k.o = q  # tpyc: warning(/copies R \| None into field/)
    print("opt_field", olen(h))


def s_opt_ret(h: H) -> Own[Optional[R]]:
    q = h.geto()
    return q  # tpyc: warning(/copies R \| None into owned storage/)


# inverse: owned results still move without a warning
def s_owned() -> None:
    ys: list[R] = []
    q = mko()
    if q is not None:
        ys.append(q)  # tpyc: ok
    w = mkr()
    ys.append(w)  # tpyc: ok
    print("owned", len(ys))


# inverse: parameter, field read and property sources alias as before
def s_sources(h: H, p: Optional[R]) -> None:
    if p is not None:
        r = p
        r.xs.append(1)
    f = h.o
    if f is not None:
        g = f
        g.xs.append(1)
    a = h.p
    if a is not None:
        b = a
        b.xs.append(1)
    print("sources", olen(h))


# match capture of the bound result
def s_match(h: H) -> None:
    q = h.geto()
    match q:
        case R() as r:
            r.xs.append(1)
        case _:
            pass
    print("match", olen(h))


# generator frame
def s_gen(h: H) -> Iterator[int32]:
    q = h.geto()
    if q is not None:
        r = q
        r.xs.append(1)
    yield olen(h)


# async frame
async def s_async(h: H) -> int32:
    q = h.geto()
    if q is not None:
        r = q
        r.xs.append(1)
    await asyncio.sleep(0)
    return olen(h)


async def amain(h: H) -> None:
    print("async", await s_async(h))


def main() -> None:
    s_dict_get()
    s_method(H())
    s_free(H())
    s_generic([R()])
    s_union(R(), S(1))
    s_readonly(H())
    s_walrus(H())
    s_assert(H())
    s_closure(H())
    s_try(H())
    s_with(H())
    try:
        n = s_error_return(H())
    except NotFound:
        print("error_return none")
    else:
        print("error_return", n)
    s_tuple(H())
    s_ternary_alias([R()], True)
    s_ternary_sink([R()], True)
    c = C(H())
    print("ctor", len(c.r.xs))
    s_append(H())
    s_own_arg(H())
    h = H()
    t = s_own_ret(h)
    print("own_ret", len(t.xs), olen(h))
    s_field(H(), K())
    s_comp(H())
    s_opt_field(H(), K())
    h2 = H()
    t2 = s_opt_ret(h2)
    print("opt_ret", len(t2.xs) if t2 is not None else -1, olen(h2))
    s_owned()
    h3 = H()
    s_sources(h3, h3.o)
    s_match(H())
    for v in s_gen(H()):
        print("gen", v)
    asyncio.run(amain(H()))


main()
