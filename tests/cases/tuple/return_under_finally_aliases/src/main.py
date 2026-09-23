# A tuple returned under a `finally` holds its owned members by alias until
# the finally has run, like the scalar `return b`: a finally mutating a member
# local shows in the returned tuple (CPython's pending return aliases), while
# every other member is still evaluated before the finally.
import asyncio
from typing import Self

from tpy import Own, ReturnException, error_return, int32


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Stop(Exception, ReturnException):
    pass


class Ctx:
    def __enter__(self) -> int32:
        return 1

    def __exit__(self, kind, value, tb) -> None:
        pass


def bump(xs: list[int32]) -> int32:
    xs.append(1)
    return len(xs)


# free function: the owned member is deferred.
def free_fn() -> tuple[Own[Box], int32]:
    b = Box(1)
    try:
        return (b, 5)  # tpyc: ok
    finally:
        b.n += 1


# whole name: `return t` of an owning tuple local.
def whole_name() -> tuple[Own[Box], int32]:
    b = Box(1)
    t = (b, 5)
    try:
        return t  # tpyc: ok
    finally:
        t[0].n += 1


# nested literal: the deferred member sits one level down.
def nested() -> tuple[tuple[Own[Box], int32], int32]:
    b = Box(1)
    try:
        return ((b, 1), 2)  # tpyc: ok
    finally:
        b.n += 1


# mixed: the owned member is deferred, the borrowed one was an alias already.
def mixed(p: Box) -> tuple[Own[Box], Box]:
    b = Box(1)
    try:
        return (b, p)  # tpyc: ok
    finally:
        b.n += 1
        p.n += 1


# a call member runs BEFORE the finally: the finally sees its side effect.
def call_member() -> tuple[Own[Box], int32]:
    b = Box(1)
    xs = [5]
    try:
        return (b, bump(xs))  # tpyc: ok
    finally:
        b.n += len(xs)


# a container member is deferred like a record one.
def container_member() -> tuple[Own[Box], Own[list[int32]]]:
    b = Box(1)
    ys = [1]
    try:
        return (b, ys)  # tpyc: ok
    finally:
        b.n += 1
        ys.append(2)


# a str member keeps the value it had at the return, as CPython's does.
def str_member() -> tuple[Own[Box], str]:
    b = Box(1)
    s = "before the finally, long enough"
    try:
        return (b, s)  # tpyc: ok
    finally:
        b.n += 1
        s = "after"


# an outer Own over the whole tuple owns each member the same way.
def outer_own() -> Own[tuple[Box, int32]]:
    b = Box(1)
    try:
        return (b, 8)  # tpyc: ok
    finally:
        b.n += 1


# context-manager body inside the try.
def with_body() -> tuple[Own[Box], int32]:
    b = Box(1)
    try:
        with Ctx():
            return (b, 1)  # tpyc: ok
    finally:
        b.n += 1


# match arm inside the try.
def match_arm(tag: int32) -> tuple[Own[Box], int32]:
    b = Box(1)
    try:
        match tag:
            case 1:
                return (b, 7)  # tpyc: ok
            case _:
                return (Box(0), 0)
    finally:
        b.n += 1


# a finally that REBINDS the member keeps the eager capture: the pending
# return holds the original object, as CPython's does.
def rebind_member() -> tuple[Own[Box], int32]:
    b = Box(1)
    try:
        return (b, 5)  # tpyc: ok
    finally:
        b = Box(99)


# the same for the scalar return.
def rebind_scalar() -> Own[Box]:
    b = Box(1)
    try:
        return b  # tpyc: ok
    finally:
        b = Box(99)


# an Own param member the finally rebinds.
def rebind_own_param(b: Own[Box]) -> tuple[Own[Box], int32]:
    try:
        return (b, 3)  # tpyc: ok
    finally:
        b = Box(98)


# a finally rebinding a DIFFERENT member's name does not affect this one.
def rebind_other(a: Box) -> tuple[Own[Box], int32]:
    b = Box(1)
    c = 4
    try:
        return (b, c)  # tpyc: ok
    finally:
        b.n += 1
        c = 40


# call members with an argument temporary: evaluated once, before the
# finally, in the order the eager return evaluates them.
def mk(k: int32) -> Own[Box]:
    return Box(k)


def peek(p: Box) -> int32:
    p.n += 1
    return p.n


def arg_temp_member() -> tuple[Own[Box], int32, int32]:
    b = Box(1)
    try:
        return (b, bump([1]), peek(mk(2)))  # tpyc: ok
    finally:
        b.n += 1


class Acc:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __iadd__(self, k: int32) -> Self:
        self.n += k
        return self


class Plus:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __add__(self, k: int32) -> Own["Plus"]:
        return Plus(self.n + k)


# in-place augmented assigns in the finally update the pending object.
def aug_record() -> tuple[Own[Acc], int32]:
    a = Acc(1)
    try:
        return (a, 1)  # tpyc: ok
    finally:
        a += 5


def aug_list() -> tuple[Own[list[int32]], int32]:
    xs = [1]
    try:
        return (xs, 2)  # tpyc: ok
    finally:
        xs += [2]


def aug_set() -> tuple[Own[set[int32]], int32]:
    s = {1}
    try:
        return (s, 3)  # tpyc: ok
    finally:
        s |= {2}


# a record with only `__add__`: `+=` rebinds, the pending return keeps the
# original object.
def aug_rebinds() -> tuple[Own[Plus], int32]:
    p = Plus(1)
    try:
        return (p, 4)  # tpyc: ok
    finally:
        p += 5


# A finally that mutates the member and then MIGHT rebind it (`if c: b =
# Box(9)`, not taken) loses the mutation, so it diverges and has no section
# here: BUGS.md#finally-mutate-then-rebind-return


# @error_return body (its tuple result cannot be bound by a caller yet --
# BUGS.md#tuple-result-error-return-bind -- so the snapshot pins its render).
@error_return(Stop)
def er_body(fail: bool) -> tuple[Own[Box], int32]:
    b = Box(1)
    try:
        if fail:
            raise Stop()
        return (b, 6)  # tpyc: ok
    finally:
        b.n += 1


class Maker:
    k: int32

    def __init__(self) -> None:
        self.k = 3

    # method body.
    def make(self) -> tuple[Own[Box], int32]:
        b = Box(1)
        try:
            return (b, self.k)  # tpyc: ok
        finally:
            b.n += 1


class Taker:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    # consuming method: the receiver is the deferred member.
    def take(self: Own[Self]) -> tuple[Own["Taker"], int32]:
        try:
            return (self, 1)  # tpyc: ok
        finally:
            self.n += 1


# closure: a nested def's own try/finally.
def closure() -> int32:
    def inner() -> tuple[Own[Box], int32]:
        b = Box(1)
        try:
            return (b, 2)  # tpyc: ok
        finally:
            b.n += 1

    r, k = inner()
    return r.n + k


# async body, with the member a frame slot across an await.
async def async_body() -> tuple[Own[Box], int32]:
    b = Box(1)
    await asyncio.sleep(0)
    try:
        return (b, 10)  # tpyc: ok
    finally:
        b.n += 1


# async body whose finally rebinds the member.
async def async_rebind() -> tuple[Own[Box], int32]:
    b = Box(1)
    await asyncio.sleep(0)
    try:
        return (b, 11)  # tpyc: ok
    finally:
        b = Box(97)


async def drive() -> None:
    r, k = await async_body()
    print("async_body", r.n, k)
    q, qk = await async_rebind()
    print("async_rebind", q.n, qk)


def main() -> None:
    a, ak = free_fn()
    print("free_fn", a.n, ak)
    w, wk = whole_name()
    print("whole_name", w.n, wk)
    r = nested()
    print("nested", r[0][0].n)
    print("nested_k", r[1])
    p = Box(10)
    m, mp = mixed(p)
    mp.n += 100
    print("mixed", m.n, mp.n, p.n)
    c, ck = call_member()
    print("call_member", c.n, ck)
    cb, ys = container_member()
    print("container_member", cb.n, len(ys))
    sb, s = str_member()
    print("str_member", sb.n, s)
    o, ok = outer_own()
    print("outer_own", o.n, ok)
    wb, wv = with_body()
    print("with_body", wb.n, wv)
    ma, mk = match_arm(1)
    print("match_arm", ma.n, mk)
    mb, mbk = Maker().make()
    print("method", mb.n, mbk)
    print("closure", closure())
    rm, rk = rebind_member()
    print("rebind_member", rm.n, rk)
    print("rebind_scalar", rebind_scalar().n)
    src = Box(1)
    ro, rok = rebind_own_param(src)
    print("rebind_own_param", ro.n, rok)
    zero = Box(0)
    rb, rc = rebind_other(zero)
    print("rebind_other", rb.n, rc)
    at, a1, a2 = arg_temp_member()
    print("arg_temp_member", at.n, a1, a2)
    ag, agk = aug_record()
    print("aug_record", ag.n, agk)
    al, alk = aug_list()
    print("aug_list", len(al), alk)
    as_, ask = aug_set()
    print("aug_set", len(as_), ask)
    ar, ark = aug_rebinds()
    print("aug_rebinds", ar.n, ark)
    tk, tkk = Taker(1).take()
    print("consuming_self", tk.n, tkk)
    asyncio.run(drive())


main()
