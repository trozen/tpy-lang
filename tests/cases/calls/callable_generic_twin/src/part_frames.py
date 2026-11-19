# The open-result local of a generator frame and of an `@error_return`
# body, at T = Q (lends), T = str and T = Own[Q]: rebound across a
# suspension, bound in a branch, first bound from an `@error_return` call
# and rebound from one (at Q and str only: an `@error_return` generic at
# `Own[Q]` is BUGS.md#generic-error-return-own-type-arg-rejects); a
# coroutine local rebound across an await; a call's result yielded in a
# tuple.
import asyncio
from typing import Iterator
from tpy import Fn, ReturnException, error_return, int32
from fixtures_q import Q, ident, mk0, own_id, sid


class E(Exception, ReturnException):
    pass


# a frame local rebound after a suspension
def gen_rebind[T](f: Fn[[T], T], a: T) -> Iterator[T]:
    y = f(a)  # tpyc: ok
    yield y
    y = f(a)
    yield y


# the same, each binding a fresh result at T = Own[Q] (`seen` binds `T`:
# BUGS.md#fn-own-return-type-param-not-inferred)
def gen_make[T](f: Fn[[], T], seen: list[T]) -> Iterator[T]:
    y = f()  # tpyc: ok
    yield y
    y = f()
    yield y


# a frame local bound in a branch, one arm `a` itself
def gen_branch[T](f: Fn[[T], T], a: T, c: bool) -> Iterator[T]:
    if c:
        y = f(a)  # tpyc: ok
    else:
        y = a
    yield y


@error_return(E)
def er_relay[T](x: T, fail: bool) -> T:
    if fail:
        raise E()
    return x


# an `@error_return` call's result as the first binding, then a rebind
@error_return(E)
def er_bind[T](a: T, again: bool) -> T:
    y = er_relay(a, False)  # tpyc: ok
    if again:
        y = er_relay(y, False)
    return y


# a frame local holding a call's open result, yielded in a tuple at its
# last use: at T = str the step takes the local's own string over
def gen_pair[T](f: Fn[[T], T], a: T) -> Iterator[tuple[T, int32]]:
    y = f(a)
    yield (y, 1)  # tpyc: ok


# a coroutine local rebound across an await (`str` only: a lent result
# cannot leave `asyncio.run`, and an `Own` coroutine result is
# BUGS.md#async-own-type-arg-ill-formed)
async def co_rebind[T](f: Fn[[T], T], a: T) -> T:
    y = f(a)  # tpyc: ok
    await asyncio.sleep(0)
    y = f(y)
    return y


def er_main(tag: str, fail: bool) -> None:
    q = Q(1)
    try:
        r = er_bind(q, fail)
        r.items.append(5)
        print(tag, len(q.items), len(r.items))
        print(tag, er_bind("long string to leave the small buffer", fail))
    except E:
        print(tag, "error")


def main() -> None:
    q = Q(1)
    for r in gen_rebind(ident, q):
        r.items.append(2)
    print("gen-rebind-ref", len(q.items))
    for s in gen_rebind(sid, "a"):
        print("gen-rebind-str", s)
    none: list[Q] = []
    for o in gen_make(mk0, none):
        o.items.append(4)
        print("gen-rebind-own", len(o.items))
    for r in gen_branch(ident, q, True):
        r.items.append(6)
    for r in gen_branch(ident, q, False):
        r.items.append(7)
    print("gen-branch-ref", len(q.items))
    for s in gen_branch(sid, "b", True):
        print("gen-branch-str-t", s)
    for s in gen_branch(sid, "b", False):
        print("gen-branch-str-f", s)
    for o in gen_branch(own_id, Q(8), True):  # tpyc: warning(/copies Q into owned storage/)
        print("gen-branch-own-t", len(o.items))
    # `y = a` leaves `y` maybe bound to `a`, so the yield copies at Own[Q],
    # as the plain twin's `return y` does (warned there too); the frame
    # twin is refused: BUGS.md#own-param-frame-branch-reseat-rejects
    for o in gen_branch(own_id, Q(8), False):  # tpyc: warning(/copies Q into owned storage/)
        print("gen-branch-own-f", len(o.items))
    for u in gen_pair(ident, q):
        u[0].items.append(9)
    print("gen-pair-ref", len(q.items))
    for t in gen_pair(sid, "a long string to leave the small buffer"):
        print("gen-pair-str", t[0], t[1])
    print("async-str", asyncio.run(co_rebind(sid, "c")))
    er_main("er-first", False)
    er_main("er-rebind", True)
