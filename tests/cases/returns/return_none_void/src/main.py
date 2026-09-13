# `return None` and bare `return` are one statement: at a void slot both emit
# `return;` (every position), and at an Optional or None-union slot both spell
# the None value.
import asyncio
from typing import Iterator
from tpy import int32, error_return, ReturnException


# free function
def free_fn() -> None:
    print("free body")
    return None  # tpyc: ok


class Rec:
    def __init__(self, n: int32) -> None:
        self.n = n
        print("init", n)
        return None  # tpyc: ok

    # method
    def bump(self) -> None:
        self.n += 1
        return None  # tpyc: ok

    # staticmethod
    @staticmethod
    def announce() -> None:
        print("static body")
        return None  # tpyc: ok


class CM:
    def __enter__(self) -> "CM":
        return self

    # __exit__ -> None (declines to suppress)
    def __exit__(self, exc_type, exc_value, tb) -> None:
        print("exit body")
        return None  # tpyc: ok


# try/finally: the finally still runs after the return
def try_finally() -> None:
    try:
        print("try body")
        return None  # tpyc: ok
    finally:
        print("try finally")


# with body: __exit__ still runs after the return
def with_body() -> None:
    with CM():
        print("with body")
        return None  # tpyc: ok


# match arm
def match_arm(k: int32) -> None:
    match k:
        case 0:
            print("match zero")
            return None  # tpyc: ok
        case _:
            print("match other")


# closure
def closure_host() -> None:
    def inner() -> None:
        print("closure inner")
        return None  # tpyc: ok

    inner()


# unannotated def (void by default)
def unannotated():
    print("unann body")
    return None  # tpyc: ok


class Bad(Exception, ReturnException):
    pass


# @error_return void body: the success value is the empty expected
@error_return(Bad)
def er_step(ok: bool) -> None:
    if not ok:
        raise Bad
    print("er body")
    return None  # tpyc: ok


# generator: `return None` ends iteration like the bare `return`
def gen() -> Iterator[int32]:
    yield 1
    yield 2
    return None  # tpyc: ok


def gen_bare() -> Iterator[int32]:
    yield 3
    return  # tpyc: ok


# async -> None
async def aret() -> None:
    await asyncio.sleep(0)
    print("async body")
    return None  # tpyc: ok


# async value-Optional slot under try/finally: the None is captured before the chain
async def opt_async_finally(k: int32) -> int32 | None:
    try:
        if k > 0:
            return k
        return  # tpyc: ok
    finally:
        print("opt_async_finally done")


async def amain() -> None:
    await aret()
    pos = await opt_async_finally(1)
    zero = await opt_async_finally(0)
    print("opt_async_finally", pos, zero)


# bare `return` at a value Optional slot spells nullopt (was `return;`)
def opt_int_bare(k: int32) -> int32 | None:
    if k > 0:
        return k
    return  # tpyc: ok


# bare `return` at a borrowed record Optional slot spells the null pointer
def opt_rec_bare(store: Rec, want: bool) -> Rec | None:
    if want:
        return store
    return  # tpyc: ok


# explicit `return None` at Optional slots keeps its value render
def opt_int_none(k: int32) -> int32 | None:
    if k > 0:
        return k
    return None  # tpyc: ok


def opt_str_none(k: int32) -> str | None:
    if k > 0:
        return "yes"
    return None  # tpyc: ok


# bare `return` at a 3-alternative None-union slot spells the None alternative
def union_bare(k: int32) -> int32 | str | None:
    if k > 0:
        return k
    if k < 0:
        return "neg"
    return  # tpyc: ok


def main() -> None:
    free_fn()
    r = Rec(1)
    r.bump()
    print("method", r.n)
    Rec.announce()
    try_finally()
    with_body()
    match_arm(0)
    match_arm(1)
    closure_host()
    unannotated()
    try:
        er_step(True)
        er_step(False)
    except Bad:
        print("er caught")
    print("gen", list(gen()), list(gen_bare()))
    asyncio.run(amain())
    print("opt_int_bare", opt_int_bare(1), opt_int_bare(0))
    store = Rec(10)
    found = opt_rec_bare(store, True)
    if found is not None:
        # the returned borrow aliases `store`, so the write shows through it
        found.n = 11
    print("opt_rec_bare", store.n, opt_rec_bare(store, False) is None)
    print("opt_int_none", opt_int_none(1), opt_int_none(0))
    print("opt_str_none", opt_str_none(1), opt_str_none(0))
    pos = union_bare(1)
    neg = union_bare(-1)
    zero = union_bare(0)
    print("union_bare", pos, neg, zero)


main()
