# A `return x` hands the body's own object over by moving it -- a value
# local, a loop variable's copy, a by-value parameter of a def, a nested def
# or a lambda, an owned parameter -- and copies anything else: a reference
# parameter, a capture, a match capture, a global, an object a `finally`
# still reads after the return, and an object a suspended iterator still
# borrows at a `yield`.
import asyncio
from typing import Callable, Iterator
from tpy import Own, ValueType, int32
from part_subclass import VBase, VSub, show_v, show_v_twin
from part_values import RBase, RSub

L = "a-string-long-enough-to-own-a-heap-buffer-xxxxx"
G = VSub(L)


def apply_r(f: Callable[[RSub], Own[RBase | int32]], r: RSub) -> None:
    u = f(r)
    # the caller's object keeps its items (a move would leave it empty);
    # the result is the warned copy, so only the caller's side is printed
    r.items.append(9)
    print("handover-lambda-ref", isinstance(u, RBase), len(r.items))


def own_param(v: Own[VSub]) -> VBase | int32:
    return v  # tpyc: warning(/narrows 'VSub'/)


def value_local() -> VBase | int32:
    v = VSub(L)
    return v  # tpyc: warning(/narrows 'VSub'/)


# a lambda's reference parameter is the caller's object: copied; an owned
# parameter and a value local are the body's own: moved
def owner() -> None:
    r = RSub()
    apply_r(lambda x: x, r)  # tpyc: warning(/narrows 'RSub'/) warning(/copies RSub/)
    show_v("handover-own-param", own_param(VSub(L)))
    show_v("handover-value-local", value_local())


def nested() -> VBase | int32:
    # a nested def's by-value parameter is its own copy: moved
    def inner(w: VSub) -> VBase | int32:
        return w  # tpyc: warning(/narrows 'VSub'/)
    return inner(VSub(L))


def captured() -> VBase | int32:
    c = VSub(L)

    # a capture is the enclosing body's object: copied
    def inner() -> VBase | int32:
        return c  # tpyc: warning(/narrows 'VSub'/)
    r = inner()
    print("handover-captured-kept", c.s)
    return r


def loop_var(xs: list[VSub]) -> VBase | int32:
    for v in xs:
        return v  # tpyc: warning(/narrows 'VSub'/)
    return 0


def match_cap(u: VSub | int32) -> VBase | int32:
    match u:
        case VSub() as v:
            return v  # tpyc: warning(/narrows 'VSub'/)
        case _:
            return 0


def global_ret() -> VBase | int32:
    return G  # tpyc: warning(/narrows 'VSub'/)


# by-value neighbours at the union: a nested def's parameter and a loop
# variable's copy move; a capture, a match capture and a global copy
def neighbours() -> None:
    show_v("handover-nested", nested())
    show_v("handover-captured", captured())
    xs = [VSub(L)]
    show_v("handover-loop", loop_var(xs))
    print("handover-loop-kept", xs[0].s)
    m = VSub(L)
    show_v("handover-match", match_cap(m))
    print("handover-match-kept", m.s)
    show_v("handover-global", global_ret())
    print("handover-global-kept", G.s)


def apply_v(f: Callable[[VSub], VBase | int32], v: VSub) -> None:
    show_v("handover-lambda-value", f(v))


async def co(v: VSub) -> VBase:
    await asyncio.sleep(0)
    return v  # tpyc: warning(/narrows 'VSub'/)


def narrowed_local(f: bool) -> VBase | int32:
    v: VSub | None = None
    if f:
        v = VSub(L)
    if v is not None:
        return v  # tpyc: warning(/narrows 'VSub'/)
    return 0


def narrowed_param(v: VSub | None) -> VBase:
    if v is not None:
        return v  # tpyc: warning(/narrows 'VSub'/)
    return VBase(L)


class CM:
    def __init__(self) -> None:
        pass

    def __enter__(self) -> int32:
        return 1

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        print("handover-with-exit")


def with_staged() -> VBase | int32:
    v = VSub(L)
    with CM():
        return v  # tpyc: warning(/narrows 'VSub'/)


# a lambda's by-value parameter moves; a coroutine's, a narrowed Optional's
# payload and a result staged past a `with` exit are copied
# (BUGS.md#dying-owned-value-staged-result-copies)
def last_use() -> None:
    p = VSub(L)
    apply_v(lambda x: x, p)  # tpyc: warning(/narrows 'VSub'/)
    print("handover-co", asyncio.run(co(p)).s)
    show_v("handover-narrowed-local", narrowed_local(True))
    show_v_twin("handover-narrowed-param", narrowed_param(VSub(L)))
    u = with_staged()
    show_v("handover-with", u)
    print("handover-last-use-kept", p.s)


def fin_local() -> VBase | int32:
    v = VSub(L)
    try:
        return v  # tpyc: warning(/narrows 'VSub'/)
    finally:
        print("handover-fin-local-reads", v.s)


def fin_param(v: VSub) -> VBase | int32:
    try:
        return v  # tpyc: warning(/narrows 'VSub'/)
    finally:
        print("handover-fin-param-reads", v.s)


async def fin_co(v: VSub) -> VBase:
    try:
        await asyncio.sleep(0)
        return v  # tpyc: warning(/narrows 'VSub'/)
    finally:
        print("handover-fin-co-reads", v.s)


# a `finally` reading the object after the return: the return copies it
def finally_reads() -> None:
    a = fin_local()
    show_v("handover-fin-local", a)
    b = fin_param(VSub(L))
    show_v("handover-fin-param", b)
    c = asyncio.run(fin_co(VSub(L)))
    print("handover-fin-co", c.s)


class VIt(ValueType):
    s: str

    def __init__(self, s: str) -> None:
        self.s = s

    def items(self) -> Iterator[str]:
        yield self.s


def first(it: Iterator[str]) -> str:
    for s in it:
        return s
    return "empty"


def gen_param(v: VIt) -> Iterator[VIt]:
    it = v.items()
    yield v  # tpyc: ok
    print("handover-gen-param-borrower", first(it))


def gen_local() -> Iterator[VIt]:
    v = VIt(L)
    it = v.items()
    yield v  # tpyc: ok
    print("handover-gen-local-borrower", first(it))


# a generator's parameter or local yielded while an iterator over it is
# suspended is copied: the iterator reads it after the yield
def yield_borrowed() -> None:
    for v in gen_param(VIt(L)):
        print("handover-gen-param", v.s)
    for w in gen_local():
        print("handover-gen-local", w.s)


def main() -> None:
    owner()
    neighbours()
    last_use()
    finally_reads()
    yield_borrowed()
