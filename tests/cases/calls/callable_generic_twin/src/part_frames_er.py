# A frame's open-result local bound inside `try`: from an `@error_return`
# call (the statement-scoped unwrap keeps the result alive until the local
# takes it) in a generator and a coroutine, from a call in a `try` body
# with the handler binding the parameter, by a walrus then a rebind (its
# run at `Own[Q]` copies, warned: callable_generic_twin_spelled) and by a
# walrus alone; the coroutine's lent result at T = Q, mutated by its
# awaiting caller.
import asyncio
from typing import Iterator
from tpy import Fn, ReturnException, error_return
from fixtures_q import Q, ident, own_id, sid


class E(Exception, ReturnException):
    pass


@error_return(E)
def er_relay[T](x: T, fail: bool) -> T:
    if fail:
        raise E()
    return x


# generator: each binding inside `try` is an `@error_return` call's result
def gen_er[T](a: T, b: T, fail: bool) -> Iterator[T]:
    y = a
    try:
        y = er_relay(a, fail)  # tpyc: ok
    except E:
        pass
    yield y
    try:
        y = er_relay(b, False)  # tpyc: ok
    except E:
        pass
    yield y


# coroutine: the same binding, read after an await
async def co_er[T](a: T, b: T) -> T:
    y = a
    try:
        y = er_relay(b, False)  # tpyc: ok
    except E:
        pass
    await asyncio.sleep(0)
    return y


# generator: a `try` body binds a result, its handler the parameter
def gen_tried[T](f: Fn[[T], T], a: T, c: bool) -> Iterator[T]:
    try:
        if c:
            raise ValueError("x")
        y = f(a)  # tpyc: ok
    except ValueError:
        y = a
    yield y


# generator: a walrus binds the result, a rebind follows a suspension
def gen_walrus[T](f: Fn[[T], T], a: T) -> Iterator[T]:
    (y := f(a))  # tpyc: ok
    yield y
    y = f(y)
    yield y


# generator: a walrus as the only binding, yielded once
def gen_walrus_once[T](f: Fn[[T], T], a: T) -> Iterator[T]:
    (y := f(a))  # tpyc: ok
    yield y


# an async caller holding the lent result, mutating it after the await
async def co_er_ref(q: Q, p: Q) -> None:
    r = await co_er(q, p)
    r.items.append(9)
    print("co-er-ref", len(q.items), len(p.items), r is p)


def main() -> None:
    q, p = Q(1), Q(2)
    for r in gen_er(q, p, False):
        r.items.append(3)
    print("frame-er-ref", len(q.items), len(p.items))
    for s in gen_er("a long string to leave the small buffer",
                    "b long string to leave the small buffer", True):
        print("frame-er-str", s)
    print("co-er-str", asyncio.run(co_er("c long string to leave the small buffer",
                                         "d long string to leave the small buffer")))
    for r in gen_tried(ident, q, True):
        r.items.append(4)
    for r in gen_tried(ident, q, False):
        r.items.append(5)
    print("frame-try-ref", len(q.items))
    for s in gen_tried(sid, "e", True):
        print("frame-try-str", s)
    # the handler's `y = a` leaves `y` maybe bound to `a`, so the yield
    # copies at Own[Q], as the twin's `return y` does (warned there too)
    for o in gen_tried(own_id, Q(6), False):  # tpyc: warning(/copies Q into owned storage/)
        o.items.append(7)
        print("frame-try-own", len(o.items))
    for r in gen_walrus(ident, q):
        r.items.append(8)
    print("frame-walrus-ref", len(q.items))
    for s in gen_walrus(sid, "f"):
        print("frame-walrus-str", s)
    for r in gen_walrus_once(ident, q):
        r.items.append(10)
    print("frame-walrus-once-ref", len(q.items))
    for s in gen_walrus_once(sid, "h"):
        print("frame-walrus-once-str", s)
    asyncio.run(co_er_ref(Q(1), Q(2)))
