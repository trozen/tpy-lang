# A union subject narrowed by isinstance / match / a ternary binds the
# member-typed extraction alias, so at a union slot it lifts that alias's
# address back into the pointer variant -- the same lift the member-typed
# twin takes and the same fact the union RETURN position already decided.
# One section per position; the callee mutates through the union slot and
# the caller reads its own object back, so a copy would show. Each section's
# call from main() passes a member-typed local, which is the twin.
# A non-mutating free function's union slot keeps MUTABLE pointees while a
# const-bound member name is `const A&`, so the readonly-method and plain
# loop-variable positions still fail the C++ build -- BUGS.md#const-member-at-mutable-pointee-union-slot.
# An `Own[A | B]` slot never reaches this lift at all (the arg predicate
# rejects an Own slot before the union verdict), so it is out of scope here.
# A closure over the narrowed subject has no section: a nested def capturing
# a narrowed union name does not lower -- BUGS.md#nested-def-narrowed-union-capture.
# The VALUE-union sections take the same fact through the value variant. Two
# of their shapes are out: the inline (ternary) form, because the check phase
# does not carry the inline narrowing
# (BUGS.md#inline-narrowed-value-union-arg-rejects), and a `str` member,
# whose alias is a view while the member is owned
# (BUGS.md#value-union-str-view-insert).
import asyncio
from typing import Iterator

from tpy import (Int32, Float64, ValueType, readonly, error_return,
                 ReturnException)


class A:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


class B:
    m: Int32

    def __init__(self, m: Int32) -> None:
        self.m = m


class Err(Exception, ReturnException):
    pass


def bump(u: A | B) -> None:
    if isinstance(u, A):
        u.n = u.n + 1
    else:
        u.m = u.m + 1


def peek(u: readonly[A | B]) -> Int32:
    if isinstance(u, A):
        return u.n
    return -1


class Sink:
    k: Int32

    def __init__(self, u: A | B) -> None:
        self.k = 0
        if isinstance(u, A):
            self.k = u.n
        bump(u)


class Guard:
    def __enter__(self) -> "Guard":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


class Relay:
    hits: Int32

    def __init__(self, v: A | B) -> None:
        self.hits = 0
        # constructor body
        if isinstance(v, A):
            bump(v)  # tpyc: ok

    # method body (the method mutates self, so its union param is not
    # inferred deep-const)
    def go(self, v: A | B) -> Int32:
        self.hits = self.hits + 1
        if isinstance(v, A):
            bump(v)  # tpyc: ok
            return v.n
        return -1


# free function body; the subject is a PARAM narrowed then passed on, the
# shape the pass-through verdict used to render bare
def free(v: A | B) -> Int32:
    if isinstance(v, A):
        bump(v)  # tpyc: ok
        return v.n
    return -1


# comprehension element
def comprehension(v: A | B) -> Int32:
    if isinstance(v, A):
        xs = [bump_and_read(v) + i for i in range(2)]  # tpyc: ok
        return xs[0] + xs[1]
    return -1


def bump_and_read(u: A | B) -> Int32:
    bump(u)
    if isinstance(u, A):
        return u.n
    return 0


# context-manager body
def with_body(v: A | B) -> Int32:
    if isinstance(v, A):
        with Guard():
            bump(v)  # tpyc: ok
            return v.n
    return -1


# try body and finally leg
def try_finally(v: A | B) -> Int32:
    k = -1
    if isinstance(v, A):
        try:
            bump(v)  # tpyc: ok
            k = v.n
        finally:
            bump(v)  # tpyc: ok
    return k


# @error_return body
@error_return(Err)
def error_body(v: A | B) -> Int32:
    if isinstance(v, A):
        bump(v)  # tpyc: ok
        return v.n
    raise Err


# match arm, class pattern
def match_arm(v: A | B) -> Int32:
    match v:
        case A():
            bump(v)  # tpyc: ok
            return v.n
        case _:
            return -1


# match arm with an as-capture (the capture is member-typed)
def match_capture(v: A | B) -> Int32:
    match v:
        case A() as got:
            bump(got)  # tpyc: ok
            return got.n
        case _:
            return -1


# generator body: the alias survives the yield, so the lift renders after it
def gen_body(v: A | B) -> Iterator[Int32]:
    if isinstance(v, A):
        yield v.n
        bump(v)  # tpyc: ok
        yield v.n


# generator body, match arm
def gen_match(v: A | B) -> Iterator[Int32]:
    match v:
        case A():
            yield v.n
            bump(v)  # tpyc: ok
            yield v.n
        case _:
            yield -1


# async body: the resumable frame re-establishes the alias per resume state,
# so the lift renders both before the suspension and after it
async def async_body(v: A | B) -> Int32:
    if isinstance(v, A):
        bump(v)  # tpyc: ok
        await asyncio.sleep(0)
        bump(v)  # tpyc: ok
        return v.n
    return -1


# async body, match arm across a suspension
async def async_match(v: A | B) -> Int32:
    match v:
        case A():
            await asyncio.sleep(0)
            bump(v)  # tpyc: ok
            return v.n
        case _:
            return -1


# inline narrowing (the ternary form)
def inline(v: A | B) -> Int32:
    return bump_and_read(v) if isinstance(v, A) else -1  # tpyc: ok


# walrus binding over a call taking the narrowed subject
def walrus(v: A | B) -> Int32:
    if isinstance(v, A):
        if (k := bump_and_read(v)) > 0:  # tpyc: ok
            return k
    return -1


# readonly[A | B] slot: the deep-const pointer-variant spelling (a const
# borrow, so nothing is mutated here)
def readonly_slot(v: A | B) -> Int32:
    if isinstance(v, A):
        return peek(v)  # tpyc: ok
    return -1


# loop variable over a list of the union, at a readonly[] slot
def loop_var(xs: list[A | B]) -> Int32:
    k = 0
    for e in xs:
        if isinstance(e, A):
            k = k + peek(e)  # tpyc: ok
    return k


# constructor-parameter slot
def ctor_slot(v: A | B) -> Int32:
    if isinstance(v, A):
        return Sink(v).k  # tpyc: ok
    return -1


# a union with a str member, narrowed to the RECORD member
def str_member(v: A | str) -> Int32:
    if isinstance(v, A):
        return str_total(v)  # tpyc: ok
    return -2


def str_total(u: A | str) -> Int32:
    if isinstance(u, A):
        return u.n
    return -3


# union RETURN slot: isinstance-narrowed and match-narrowed both take the
# address of the alias
def pick_isinstance(v: A | B) -> A | B:
    if isinstance(v, A):
        return v  # tpyc: ok
    return v


def pick_match(v: A | B) -> A | B:
    match v:
        case A():
            return v  # tpyc: ok
        case _:
            return v


# VALUE union: the narrowed name binds the member alias and the value
# variant's converting ctor takes it, so the arg passes BARE -- no
# `std::variant<...> __tmp_N`. A value union cannot show aliasing (its members
# are copies by definition), so the twin renders on the same line instead: the
# member-typed local at the same slot, which takes the variant-temp row.
def value_union(v: Int32 | Float64) -> Int32:
    if isinstance(v, Int32):
        return vu_total(v)  # tpyc: ok
    return -1


# value union, match arm
def value_union_match(v: Int32 | Float64) -> Int32:
    match v:
        case Int32():
            return vu_total(v)  # tpyc: ok
        case _:
            return -1


def vu_total(u: Int32 | Float64) -> Int32:
    if isinstance(u, Int32):
        return u + 1
    return -2


def vu_peek(u: readonly[Int32 | Float64]) -> Int32:
    if isinstance(u, Int32):
        return u
    return -2


class VuBox:
    base: Int32

    def __init__(self, base: Int32) -> None:
        self.base = base

    def go(self, u: Int32 | Float64) -> Int32:
        if isinstance(u, Int32):
            return self.base + u
        return -2


class VuSink:
    k: Int32

    def __init__(self, u: Int32 | Float64) -> None:
        self.k = vu_total(u)


# value union at a METHOD slot
def value_union_method(v: Int32 | Float64, box: VuBox) -> Int32:
    if isinstance(v, Int32):
        return box.go(v)  # tpyc: ok
    return -1


# value union at a CTOR slot
def value_union_ctor(v: Int32 | Float64) -> Int32:
    if isinstance(v, Int32):
        return VuSink(v).k  # tpyc: ok
    return -1


# value union at a comprehension element (a position with no flush slot, so
# the temp row could never have served it)
def value_union_comp(v: Int32 | Float64) -> Int32:
    if isinstance(v, Int32):
        xs = [vu_total(v) + i for i in range(2)]  # tpyc: ok
        return xs[0] + xs[1]
    return -1


# value union at a readonly[...] slot
def value_union_readonly(v: Int32 | Float64) -> Int32:
    if isinstance(v, Int32):
        return vu_peek(v)  # tpyc: ok
    return -1


def big_total(u: int | Float64) -> int:
    if isinstance(u, int):
        return u + 1
    return -2


# value union with a BigInt member, narrowed to the BigInt
def value_union_big(v: int | Float64) -> int:
    if isinstance(v, int):
        return big_total(v)  # tpyc: ok
    return -1


class Pt(ValueType):
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x


def pt_total(u: Pt | Float64) -> Int32:
    if isinstance(u, Pt):
        return u.x + 1
    return -2


# value union with a ValueType-RECORD member, narrowed to the record
def value_union_record(v: Pt | Float64) -> Int32:
    if isinstance(v, Pt):
        return pt_total(v)  # tpyc: ok
    return -1


# inverse: a union-DECLARED name whose read type sema retyped to a member by
# assignment is still the variant in C++, so it passes bare (no lift)
def assign_narrowed() -> Int32:
    x: A | B = A(50)
    bump(x)  # tpyc: ok
    return peek(x)


async def async_main() -> None:
    t = A(140)
    print("async", await async_body(t), t.n)
    u = A(150)
    print("async-match", await async_match(u), u.n)


def main() -> None:
    a = A(1)
    print("free", free(a), a.n)

    b = A(10)
    r = Relay(b)
    print("ctor-body", b.n, r.hits)

    # a method's union arg slot has no member lift row, so the method
    # section's caller passes an already-union name (which renders bare)
    mv: A | B = A(15)
    print("method", r.go(mv), peek(mv), r.hits)

    c = A(20)
    print("comprehension", comprehension(c), c.n)

    d = A(30)
    print("with", with_body(d), d.n)

    e = A(40)
    print("try-finally", try_finally(e), e.n)

    f = A(50)
    try:
        print("error-return", error_body(f), f.n)
    except Err:
        print("error-return raised")

    g = A(60)
    print("match", match_arm(g), g.n)

    h = A(70)
    print("match-capture", match_capture(h), h.n)

    gt = 0
    gv = A(160)
    for y in gen_body(gv):
        gt = gt + y
    print("generator", gt, gv.n)

    gt2 = 0
    gw = A(170)
    for y in gen_match(gw):
        gt2 = gt2 + y
    print("gen-match", gt2, gw.n)

    i = A(80)
    print("inline", inline(i), i.n)

    j = A(90)
    print("walrus", walrus(j), j.n)

    k = A(100)
    print("readonly", readonly_slot(k), k.n)

    xs: list[A | B] = [A(1), A(2)]
    print("loop-var", loop_var(xs))

    p = A(110)
    print("ctor-slot", ctor_slot(p), p.n)

    q = A(120)
    print("str-member", str_member(q))

    s = A(130)
    ret = pick_isinstance(s)
    if isinstance(ret, A):
        print("return-isinstance", ret.n)
    ret2 = pick_match(s)
    if isinstance(ret2, A):
        print("return-match", ret2.n)

    # Int32(...) is spelled out because CPython's Int32 stub is an int
    # SUBCLASS: a bare literal would not satisfy isinstance there
    vv = Int32(180)
    print("value-union", value_union(vv), vu_total(vv))
    print("value-union-match", value_union_match(vv), vu_total(vv))
    box = VuBox(1)
    print("value-union-method", value_union_method(vv, box), box.go(vv))
    print("value-union-ctor", value_union_ctor(vv), VuSink(vv).k)
    print("value-union-comp", value_union_comp(vv), vu_total(vv))
    print("value-union-readonly", value_union_readonly(vv), vu_peek(vv))
    # annotated because a bare literal types as Int32: the twin must be the
    # union's exact BigInt member to take the variant-temp row
    bb: int = 190
    print("value-union-big", value_union_big(bb), big_total(bb))
    pp = Pt(3)
    print("value-union-record", value_union_record(pp), pt_total(pp))

    print("assign-narrowed", assign_narrowed())

    asyncio.run(async_main())


main()

# module-level statement
G: A | B = A(200)

if isinstance(G, A):
    bump(G)  # tpyc: ok
    print("module", G.n)
