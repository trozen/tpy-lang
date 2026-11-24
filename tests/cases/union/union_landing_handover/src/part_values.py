# A value-type subclass object handed over as its base keeps the object
# it came from while that lives and moves only a dying one: a select is a
# live object unless some arm is fresh (nested, an element arm, an
# `or` select, at a generator's and an awaited coroutine's argument), a
# fresh temporary moves, a by-value or an owned parameter returned at its
# last use moves, a reference parameter returned into owned storage is
# copied (a lambda's too), a tuple literal copies a live name, and an
# in-place fallback's result is stored as an assignment's.
import asyncio
from typing import Callable, Iterator
from tpy import Own, int32, int64
from part_subclass import VBase, VSub, show_v, show_v_twin

L = "a-string-long-enough-to-own-a-heap-buffer-xxxxx"


def mk() -> VSub:
    return VSub("a-fresh-string-long-enough-to-own-a-buffer-x")


# nested selects, an element arm and an `or` select over live objects are
# copied into the base, at the union parameter and at the scalar twin
def selects(f: bool, g: bool) -> None:
    a = VSub(L)
    b = VSub(L)
    c = VSub(L)
    show_v("values-nested-then", (a if f else b) if g else c)  # tpyc: ok
    show_v_twin("values-nested-then-twin", (a if f else b) if g else c)
    show_v("values-nested-else", a if f else (b if g else c))  # tpyc: ok
    show_v_twin("values-nested-else-twin", a if f else (b if g else c))
    xs = [VSub(L)]
    show_v("values-elem-arm", xs[0] if f else b)  # tpyc: ok
    show_v_twin("values-elem-arm-twin", xs[0] if f else b)
    show_v("values-or", a or b)  # tpyc: ok
    show_v_twin("values-or-twin", a or b)
    # a fresh arm makes the select a prvalue: only that value moves
    show_v("values-mixed", a if f else mk())  # tpyc: ok
    show_v_twin("values-mixed-twin", a if f else mk())
    print("values-selects-kept", a.s, b.s, c.s, xs[0].s)


def gen(u: VBase | int32) -> Iterator[int32]:
    if isinstance(u, VBase):
        print("values-gen", u.s)
    yield 1


def consume(it: Iterator[int32]) -> None:
    for _ in it:
        pass


async def co(u: VBase | int32) -> int32:
    await asyncio.sleep(0)
    if isinstance(u, VBase):
        print("values-co", u.s)
    return 1


async def awaited(f: bool, a: VSub, b: VSub) -> None:
    # a select at an awaited coroutine's argument
    await co(a if f else b)  # tpyc: ok
    print("values-co-kept", a.s, b.s)


# resumable bodies: a select at a generator factory's argument and at an
# awaited coroutine's argument
def resumable(f: bool) -> None:
    a = VSub(L)
    b = VSub(L)
    consume(gen(a if f else b))  # tpyc: ok
    print("values-gen-kept", a.s, b.s)
    asyncio.run(awaited(f, a, b))


# a fresh temporary is moved into the base, at the union and the twin
def fresh() -> None:
    show_v("values-fresh", VSub("a-temporary-string-long-enough-to-own-one"))  # tpyc: ok
    show_v_twin("values-fresh-twin", VSub("a-temporary-twin-string-long-enough-x"))


# a by-value parameter is the callee's own copy: returned at its last use
# it is moved into the base
def ret_param(v: VSub) -> VBase | int32:
    return v  # tpyc: warning(/narrows 'VSub'/)


def ret_param_twin(v: VSub) -> VBase:
    return v  # tpyc: warning(/narrows 'VSub'/)


# an owned parameter at its last use moves the same way
def ret_owned(v: Own[VSub]) -> VBase | int32:
    return v  # tpyc: warning(/narrows 'VSub'/)


def ret_owned_twin(v: Own[VSub]) -> VBase:
    return v  # tpyc: warning(/narrows 'VSub'/)


def params() -> None:
    p = VSub(L)
    show_v("values-ret-param", ret_param(p))
    show_v_twin("values-ret-param-twin", ret_param_twin(p))
    show_v("values-ret-owned", ret_owned(VSub(L)))
    show_v_twin("values-ret-owned-twin", ret_owned_twin(VSub(L)))
    print("values-params-kept", p.s)


class RBase:
    items: list[int32]

    def __init__(self) -> None:
        self.items = [1, 2, 3]


class RSub(RBase):
    pass


# (its scalar twin `-> Own[RBase]` is refused: BUGS.md#own-subclass-return-rejects)
def ret_ref(r: RSub) -> Own[RBase | int32]:
    return r  # tpyc: warning(/narrows 'RSub'/) warning(/copies RSub/)


def apply_u(f: Callable[[RSub], Own[RBase | int32]], v: RSub) -> None:
    r = f(v)
    print("values-lambda-union", isinstance(r, int32), len(v.items))


def apply_opt(f: Callable[[RSub], Own[RBase | None]], v: RSub) -> None:
    r = f(v)
    print("values-lambda-optional", r is None, len(v.items))


# a reference parameter returned into owned storage is copied, never
# moved out of the caller's object: a def's and a lambda's
def references() -> None:
    v = RSub()
    r = ret_ref(v)
    print("values-ret-ref", isinstance(r, int32), len(v.items))
    apply_u(lambda x: x, v)  # tpyc: warning(/narrows 'RSub'/) warning(/copies RSub/)
    apply_opt(lambda x: x, v)  # tpyc: warning(/narrows 'RSub'/) warning(/copies RSub/)
    v.items.append(4)
    print("values-references-kept", len(v.items))


# a tuple literal holding a live name copies it into the union element
def tuple_literal() -> None:
    v = VSub(L)
    # (reading `t` back is BUGS.md#value-union-tuple-element-reads-reject)
    t: tuple[VBase | int32, int32] = (v, 2)  # tpyc: warning(/narrows 'VSub'/)
    print("values-tuple", v.s)


class Val:
    n: int64

    def __init__(self, n: int64) -> None:
        self.n = n

    def __mul__(self, other: int64 | str) -> int64:
        if isinstance(other, str):
            return self.n * 100
        return self.n * other


def make_val() -> int64:
    print("values-make-val")
    return 3


# a fresh call operand at a value-union dunder parameter binds in place:
# evaluated once, and only when the guard before it holds
def operands(f: bool) -> None:
    a = Val(2)
    r = a * make_val()  # tpyc: ok
    print("values-mul", r)
    if f and a * make_val() > 0:  # tpyc: ok
        print("values-mul-guard")
    if not f and a * make_val() > 0:
        print("values-mul-never")


class RV:
    def __init__(self) -> None:
        pass

    def __radd__(self, other: VBase | int32) -> VSub:
        if isinstance(other, VBase):
            return VSub(other.s + "-radd")
        return VSub("int")


# an in-place fallback stores the dunder's result into the target as
# `v = v + RV()` does: the subclass result is upcast, warned the same way
def aug_result() -> None:
    v: VBase = VSub(L)  # tpyc: warning(/narrows 'VSub'/)
    v += RV()  # tpyc: warning(/narrows 'VSub'/)
    print("values-aug-result", v.s)


def main() -> None:
    selects(True, True)
    selects(False, False)
    resumable(True)
    resumable(False)
    fresh()
    params()
    references()
    tuple_literal()
    operands(True)
    aug_result()
