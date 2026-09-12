# Narrowed union members pass through union argument and return slots.
# Mutation exposes reference copies; value members retain value semantics.
# The `total`-calling sections below cover the other half: a non-mutating
# union parameter borrows CONST pointees, so const-bound sources reach it
# and each source's binding picks its own conversion.
import asyncio
from typing import Iterator

from tpy import (int32, float64, Own, ValueType, readonly, error_return,
                 ReturnException)


class A:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class B:
    m: int32

    def __init__(self, m: int32) -> None:
        self.m = m


class Err(Exception, ReturnException):
    pass


def bump(u: A | B) -> None:
    if isinstance(u, A):
        u.n = u.n + 1
    else:
        u.m = u.m + 1


def peek(u: readonly[A | B]) -> int32:
    if isinstance(u, A):
        return u.n
    return -1


class Sink:
    k: int32

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
    hits: int32

    def __init__(self, v: A | B) -> None:
        self.hits = 0
        # constructor body
        if isinstance(v, A):
            bump(v)  # tpyc: ok

    # method body (the method mutates self, so its union param is not
    # inferred deep-const)
    def go(self, v: A | B) -> int32:
        self.hits = self.hits + 1
        if isinstance(v, A):
            bump(v)  # tpyc: ok
            return v.n
        return -1


# free function body; the subject is a PARAM narrowed then passed on, the
# shape the pass-through verdict used to render bare
# Nested captures cannot lower: BUGS.md#nested-def-narrowed-union-capture.
def free(v: A | B) -> int32:
    if isinstance(v, A):
        bump(v)  # tpyc: ok
        return v.n
    return -1


# comprehension element
def comprehension(v: A | B) -> int32:
    if isinstance(v, A):
        xs = [bump_and_read(v) + i for i in range(2)]  # tpyc: ok
        return xs[0] + xs[1]
    return -1


def bump_and_read(u: A | B) -> int32:
    bump(u)
    if isinstance(u, A):
        return u.n
    return 0


# context-manager body
def with_body(v: A | B) -> int32:
    if isinstance(v, A):
        with Guard():
            bump(v)  # tpyc: ok
            return v.n
    return -1


# try body and finally leg
def try_finally(v: A | B) -> int32:
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
def error_body(v: A | B) -> int32:
    if isinstance(v, A):
        bump(v)  # tpyc: ok
        return v.n
    raise Err


# match arm, class pattern
def match_arm(v: A | B) -> int32:
    match v:
        case A():
            bump(v)  # tpyc: ok
            return v.n
        case _:
            return -1


# match arm with an as-capture (the capture is member-typed)
def match_capture(v: A | B) -> int32:
    match v:
        case A() as got:
            bump(got)  # tpyc: ok
            return got.n
        case _:
            return -1


# generator body: the alias survives the yield, so the lift renders after it
def gen_body(v: A | B) -> Iterator[int32]:
    if isinstance(v, A):
        yield v.n
        bump(v)  # tpyc: ok
        yield v.n


# generator body, match arm
def gen_match(v: A | B) -> Iterator[int32]:
    match v:
        case A():
            yield v.n
            bump(v)  # tpyc: ok
            yield v.n
        case _:
            yield -1


# async body: the resumable frame re-establishes the alias per resume state,
# so the lift renders both before the suspension and after it
async def async_body(v: A | B) -> int32:
    if isinstance(v, A):
        bump(v)  # tpyc: ok
        await asyncio.sleep(0)
        bump(v)  # tpyc: ok
        return v.n
    return -1


# async body, match arm across a suspension
async def async_match(v: A | B) -> int32:
    match v:
        case A():
            await asyncio.sleep(0)
            bump(v)  # tpyc: ok
            return v.n
        case _:
            return -1


# inline narrowing (the ternary form)
def inline(v: A | B) -> int32:
    return bump_and_read(v) if isinstance(v, A) else -1  # tpyc: ok


# walrus binding over a call taking the narrowed subject
def walrus(v: A | B) -> int32:
    if isinstance(v, A):
        if (k := bump_and_read(v)) > 0:  # tpyc: ok
            return k
    return -1


# readonly[A | B] slot: the deep-const pointer-variant spelling (a const
# borrow, so nothing is mutated here). The plain `A | B` slots below render
# the same way, so the annotation changes nothing about the arg.
def readonly_slot(v: A | B) -> int32:
    if isinstance(v, A):
        return peek(v)  # tpyc: ok
    return -1


# loop variable over a list of the union, at a readonly[] slot
def loop_var(xs: list[A | B]) -> int32:
    k = 0
    for e in xs:
        if isinstance(e, A):
            k = k + peek(e)  # tpyc: ok
    return k


# constructor-parameter slot
# Own union slots reject before the union lift, so this slot is borrowed.
def ctor_slot(v: A | B) -> int32:
    if isinstance(v, A):
        return Sink(v).k  # tpyc: ok
    return -1


# a union with a str member, narrowed to the RECORD member
def str_member(v: A | str) -> int32:
    if isinstance(v, A):
        return str_total(v)  # tpyc: ok
    return -2


def str_total(u: A | str) -> int32:
    if isinstance(u, A):
        return u.n
    return -3


# A non-mutating union parameter borrows CONST pointees, so every
# const-bound source below reaches `total`'s slot; `bump`'s mutating slot
# keeps the mutable ones, which is what the inverse sections pin.
def total(u: A | B) -> int32:
    if isinstance(u, A):
        return u.n
    return -1


# free function: a const-bound member-typed name at the deep-const slot
def const_member(a: A) -> int32:
    return total(a)  # tpyc: ok


class Reader:
    base: int32

    def __init__(self, base: int32) -> None:
        self.base = base

    # method body: the method mutates nothing, so its narrowed subject binds
    # `const A&` and its own union param is deep-const too
    def read(self, v: A | B) -> int32:
        if isinstance(v, A):
            return self.base + total(v)  # tpyc: ok
        return -1


class Tally:
    k: int32

    # constructor parameter, forwarded to a second deep-const slot
    def __init__(self, u: A | B) -> None:
        self.k = total(u)  # tpyc: ok


# constructor CALL arg: the ctor loop threads the callee verdict the free
# loop threads
def ctor_call_arg(a: A) -> int32:
    return Tally(a).k  # tpyc: ok


# Own[union] name forward: the source is the STORAGE variant, so the lift is
# the storage converter rather than the borrow one
def own_forward(u: Own[A | B]) -> int32:  # tpyc: warning(/never consumed/)
    return total(u)  # tpyc: ok


# loop variable over list[A | B]: a storage binding as well, un-narrowed
def loop_const(xs: list[A | B]) -> int32:
    k = 0
    for e in xs:
        k = k + total(e)  # tpyc: ok
    return k


# generator factory param
def gen_total(v: A | B) -> Iterator[int32]:
    yield total(v)  # tpyc: ok
    yield total(v)


# async factory param
async def async_total(v: A | B) -> int32:
    await asyncio.sleep(0)
    return total(v)  # tpyc: ok


# storage source at a METHOD slot: the element binding takes the storage
# converter there too, not just at a free call
def loop_method(xs: list[A | B], r: Reader) -> int32:
    k = 0
    for e in xs:
        k = k + r.read(e)  # tpyc: ok
    return k


# closure body: the capture keeps the ENCLOSING parameter's const verdict, so
# the narrowing inside the lambda spells const pointees
def closure_narrow(u: A | B) -> int32:
    def inner() -> int32:
        if isinstance(u, A):  # tpyc: ok
            return u.n
        return -1
    return inner()


# closure body, forwarding the capture on: already const, so no conversion
def closure_forward(u: A | B) -> int32:
    def inner() -> int32:
        return total(u)  # tpyc: ok
    return inner()


# inverse: a MUTABLE borrow source converts with the type's own as_const(),
# and the mutating slot before it keeps the mutable pointees -- the caller
# observes the mutation through the boundary
def wrap_then_mutate(u: A | B) -> int32:
    bump(u)  # tpyc: ok
    return total(u)  # tpyc: ok


# inverse: both ends non-mutating, so the forward needs no conversion at all
def forward_union(u: A | B) -> int32:
    return total(u)  # tpyc: ok


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
# Inline narrowing is lost: BUGS.md#inline-narrowed-value-union-arg-rejects.
# str aliases are views, unlike owned members: BUGS.md#value-union-str-view-insert.
def value_union(v: int32 | float64) -> int32:
    if isinstance(v, int32):
        return vu_total(v)  # tpyc: ok
    return -1


# value union, match arm
def value_union_match(v: int32 | float64) -> int32:
    match v:
        case int32():
            return vu_total(v)  # tpyc: ok
        case _:
            return -1


def vu_total(u: int32 | float64) -> int32:
    if isinstance(u, int32):
        return u + 1
    return -2


def vu_peek(u: readonly[int32 | float64]) -> int32:
    if isinstance(u, int32):
        return u
    return -2


class VuBox:
    base: int32

    def __init__(self, base: int32) -> None:
        self.base = base

    def go(self, u: int32 | float64) -> int32:
        if isinstance(u, int32):
            return self.base + u
        return -2


class VuSink:
    k: int32

    def __init__(self, u: int32 | float64) -> None:
        self.k = vu_total(u)


# value union at a METHOD slot
def value_union_method(v: int32 | float64, box: VuBox) -> int32:
    if isinstance(v, int32):
        return box.go(v)  # tpyc: ok
    return -1


# value union at a CTOR slot
def value_union_ctor(v: int32 | float64) -> int32:
    if isinstance(v, int32):
        return VuSink(v).k  # tpyc: ok
    return -1


# value union at a comprehension element (a position with no flush slot, so
# the temp row could never have served it)
def value_union_comp(v: int32 | float64) -> int32:
    if isinstance(v, int32):
        xs = [vu_total(v) + i for i in range(2)]  # tpyc: ok
        return xs[0] + xs[1]
    return -1


# value union at a readonly[...] slot
def value_union_readonly(v: int32 | float64) -> int32:
    if isinstance(v, int32):
        return vu_peek(v)  # tpyc: ok
    return -1


def big_total(u: int | float64) -> int:
    if isinstance(u, int):
        return u + 1
    return -2


# value union with a BigInt member, narrowed to the BigInt
def value_union_big(v: int | float64) -> int:
    if isinstance(v, int):
        return big_total(v)  # tpyc: ok
    return -1


class Pt(ValueType):
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


def pt_total(u: Pt | float64) -> int32:
    if isinstance(u, Pt):
        return u.x + 1
    return -2


# value union with a ValueType-RECORD member, narrowed to the record
def value_union_record(v: Pt | float64) -> int32:
    if isinstance(v, Pt):
        return pt_total(v)  # tpyc: ok
    return -1


# inverse: a union-DECLARED name whose read type sema retyped to a member by
# assignment is still the variant in C++, so it passes bare (no lift)
def assign_narrowed() -> int32:
    x: A | B = A(50)
    bump(x)  # tpyc: ok
    return peek(x)


async def async_main() -> None:
    t = A(140)
    print("async", await async_body(t), t.n)
    u = A(150)
    print("async-match", await async_match(u), u.n)
    w: A | B = A(240)
    print("async-const", await async_total(w))


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

    cm = A(210)
    print("const-member", const_member(cm), Reader(1000).read(mv))
    print("ctor-call-arg", ctor_call_arg(cm), own_forward(A(220)))
    cxs: list[A | B] = [A(1), A(2)]
    print("loop-const", loop_const(cxs), loop_method(cxs, Reader(1000)))
    print("closure", closure_narrow(mv), closure_forward(mv))
    gt3 = 0
    for y in gen_total(cm):
        gt3 = gt3 + y
    print("gen-const", gt3)
    wm: A | B = A(230)
    print("as-const", wrap_then_mutate(wm), forward_union(wm), total(wm))

    s = A(130)
    ret = pick_isinstance(s)
    # A post-return mutation must remain visible through the returned alias.
    # Field writes preserve this record borrow: BUGS.md#record-field-borrow-false-invalidation.
    s.n = s.n + 1  # tpyc: warning(/Mutation of 's' while borrowed/)
    if isinstance(ret, A):
        print("return-isinstance", ret.n, s.n)
    ret2 = pick_match(s)
    # The match return must preserve the same shared object.
    # Field writes preserve this record borrow: BUGS.md#record-field-borrow-false-invalidation.
    s.n = s.n + 1  # tpyc: warning(/Mutation of 's' while borrowed/)
    if isinstance(ret2, A):
        print("return-match", ret2.n, s.n)

    # int32(...) is spelled out because CPython's int32 stub is an int
    # SUBCLASS: a bare literal would not satisfy isinstance there
    vv = int32(180)
    print("value-union", value_union(vv), vu_total(vv))
    print("value-union-match", value_union_match(vv), vu_total(vv))
    box = VuBox(1)
    print("value-union-method", value_union_method(vv, box), box.go(vv))
    print("value-union-ctor", value_union_ctor(vv), VuSink(vv).k)
    print("value-union-comp", value_union_comp(vv), vu_total(vv))
    print("value-union-readonly", value_union_readonly(vv), vu_peek(vv))
    # annotated because a bare literal types as int32: the twin must be the
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
