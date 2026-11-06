# The callable-call sections at every covered position: method, constructor,
# generator, async, comprehension, closure, match / try, @error_return, field
# / subscript / call-result callees and a rebind.
from typing import Callable, Iterator
from tpy import Fn, int32, copy, error_return
import asyncio
from fixtures import P, Lender, Holder, Failed, ident, make_cb, ret, via0


# method: self-capturing lambda and a self-lending __call__ at an Fn slot.
class Box:
    p: P

    def __init__(self) -> None:
        self.p = P(10)

    def bump(self) -> None:
        via0(lambda: self.p)  # tpyc: ok
        via0(Lender())
        r = ret(ident, self.p)  # tpyc: ok
        r.n += 1


def sec_method() -> None:
    b = Box()
    b.bump()
    s = Lender()
    via0(s)
    print("method:", b.p.n, s.p.n)


# constructor (a Callable parameter: an Fn one is
# BUGS.md#plain-ctor-fn-param-no-template-head).
class Keep:
    seen: int32

    def __init__(self, f: Callable[[P], P], x: P) -> None:
        r = f(x)  # tpyc: ok
        r.n += 1
        self.seen = r.n


def sec_ctor() -> None:
    p = P(1)
    k = Keep(ident, p)
    print("ctor:", p.n, k.seen)


# generator: the yield lends what the callable lends.
def gen(f: Fn[[P], P], xs: list[P]) -> Iterator[P]:
    for x in xs:
        yield f(x)  # tpyc: ok


def sec_gen() -> None:
    xs = [P(1), P(2)]
    for y in gen(ident, xs):
        y.n += 10
    print("gen:", xs[0].n, xs[1].n)


# async: a borrow of an argument inside a coroutine body.
async def bump_async(f: Callable[[P], P], x: P) -> int32:
    r = f(x)  # tpyc: ok
    r.n += 1
    return r.n


def sec_async() -> None:
    p = P(1)
    got = asyncio.run(bump_async(ident, p))
    print("async:", p.n, got)


# comprehension: the element reads through the borrow, as `ident(x).n` does.
def sec_comp() -> None:
    xs = [P(1)]
    f: Callable[[P], P] = ident
    ns = [f(x).n for x in xs]  # tpyc: ok
    ys = [copy(f(x)) for x in xs]  # tpyc: ok
    ys[0].n += 1
    print("comp:", xs[0].n, ns[0], ys[0].n)


# closure: a nested def calling the callable.
def sec_closure() -> None:
    p = P(1)
    f: Callable[[P], P] = ident

    def inner() -> None:
        r = f(p)  # tpyc: ok
        r.n += 1
    inner()
    print("closure:", p.n)


# match arm and try/finally bodies.
def sec_match_try(k: int32) -> None:
    p = P(1)
    f: Callable[[P], P] = ident
    match k:
        case 1:
            r = f(p)  # tpyc: ok
            r.n += 1
        case _:
            pass
    q = P(2)
    try:
        r2 = f(q)  # tpyc: ok
        r2.n += 10
    finally:
        print("match/try:", p.n, q.n)


# @error_return body.
@error_return(Failed)
def checked(f: Fn[[P], P], x: P) -> int32:
    r = f(x)  # tpyc: ok
    r.n += 1
    if r.n < 0:
        raise Failed
    return r.n


def sec_error_return() -> None:
    p = P(1)
    try:
        v = checked(ident, p)
    except Failed:
        v = -1
    print("error_return:", p.n, v)


# field, subscript and call-result callees; a rebind keeps aliasing.
def through_field(x: P) -> P:
    holder = Holder(lambda q: q)
    return holder.cb(x)  # tpyc: ok


def sec_callees() -> None:
    p = P(1)
    h = Holder(lambda q: q)
    h.cb(p).n += 1
    cbs: list[Callable[[P], P]] = [lambda q: q]
    cbs[0](p).n += 10
    make_cb()(p).n += 100
    through_field(p).n += 1000
    print("callees:", p.n)


def sec_rebind() -> None:
    p = P(1)
    a = P(0)
    f: Callable[[P], P] = ident
    r = f(p)
    r.n += 1
    r = f(a)  # tpyc: ok
    r.n += 1
    print("rebind:", p.n, a.n)


def main() -> None:
    sec_method()
    sec_ctor()
    sec_gen()
    sec_async()
    sec_comp()
    sec_closure()
    sec_match_try(1)
    sec_error_return()
    sec_callees()
    sec_rebind()
