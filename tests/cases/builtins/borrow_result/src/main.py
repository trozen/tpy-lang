# min / max with key= over two or three operands hand back the OPERAND
# itself, not a copy: every section writes through the result and reads the
# operand back.
import asyncio
from typing import Iterator
from tpy import Own, copy, error_return, readonly, ReturnException


class P:
    v: int

    def __init__(self, v: int) -> None:
        self.v = v

    def __str__(self) -> str:
        return f"P{self.v}"


def key_of(p: P) -> int:
    return p.v


def bump(p: P) -> None:
    p.v += 1000


class Team:
    lead: P
    a: P
    b: P

    def __init__(self, a: P, b: P) -> None:
        self.a = P(a.v)
        self.b = P(b.v)
        # constructor: an owning field copies, with the warning
        self.lead = max(a, b, key=key_of)  # tpyc: warning(/copies P/)

    def low(self) -> P:
        # method: a returned borrow of a field
        return min(self.a, self.b, key=key_of)


# free function: local binding, argument, nested call, inline read
def free_fn() -> None:
    a = P(1)
    b = P(2)
    c = P(3)
    m = min(a, b, key=key_of)
    m.v = 10
    print("free min2", a.v, b.v)
    x = max(a, b, c, key=key_of)
    x.v = 20
    print("free max3", a.v, b.v, c.v)
    bump(min(a, b, key=key_of))
    print("free arg", a.v, b.v)
    n = min(min(a, b, key=key_of), c, key=key_of)
    n.v = 5
    print("free nested", a.v, b.v, c.v)
    print("free read", max(a, b, key=key_of).v)


# the call itself counts as a mutable use of its operands: it hands back a
# reference to one of them, and whether anything writes through it is not
# inspected -- so the parameters are mutable even when the body only reads,
# as `def pick[T](a: T, b: T) -> T` already makes its caller's; declaring
# them readonly keeps them const (`ro_operands`)
def read_only(a: P, b: P) -> int:
    m = min(a, b, key=key_of)
    return m.v


# a mutable return of the result hands the operand itself to the caller
def pick_low(a: P, b: P) -> P:
    return min(a, b, key=key_of)


# a readonly RETURN does not make the result read-only at the call: the
# parameters are mutable references here too
def peek_low(a: P, b: P) -> readonly[P]:
    return min(a, b, key=key_of)


# readonly operands give a readonly result
def ro_operands(a: readonly[P], b: readonly[P]) -> int:
    m = max(a, b, key=lambda p: p.v)  # tpyc: type(readonly[P])
    return m.v


# a readonly operand beside an operand that lends nothing: the in-place
# result is read-only, a reference into the operands for the statement
def ro_temp(a: readonly[P]) -> int:
    r = min(a, W().ro(), key=lambda p: p.v).v  # tpyc: ok
    return r


# generator body: the borrow lives across a suspension
def gen_body(a: P, b: P) -> Iterator[int]:
    m = min(a, b, key=key_of)
    yield m.v
    m.v = 77
    yield m.v
    # a walrus in a frame condition holds the operand as the binding does
    if (w := max(a, b, key=key_of)).v > 0:  # tpyc: ok
        w.v += 1


# async body
async def async_body(a: P, b: P) -> int:
    m = max(a, b, key=key_of)
    await asyncio.sleep(0)
    m.v = 88
    if (w := max(a, b, key=key_of)).v > 0:  # tpyc: ok
        await asyncio.sleep(0)
        w.v += 1
    return m.v


# closure: the operands are captured names
def closure_fn() -> None:
    a = P(1)
    b = P(2)

    def inner() -> None:
        m = min(a, b, key=key_of)
        m.v = 33

    inner()
    print("closure", a.v, b.v)


# try/finally and a branch-first binding
def try_fn(flag: bool) -> None:
    a = P(1)
    b = P(2)
    try:
        m = min(a, b, key=key_of)
        m.v = 44
    finally:
        print("try", a.v, b.v)
    if flag:
        w = max(a, b, key=key_of)
    else:
        w = min(a, b, key=key_of)
    w.v = 55
    print("branch", a.v, b.v)


# comprehension: an inline read per element
def comp_fn() -> None:
    xs = [P(1), P(5)]
    ys = [P(2), P(4)]
    print("comp", [min(xs[i], ys[i], key=key_of).v for i in range(2)])
    # the same element read, then written through in place
    for i in range(2):
        bump(min(xs[i], ys[i], key=key_of))
    print("comp after", [p.v for p in xs], [p.v for p in ys])


# Owned element slots: each holds a warned copy of the operand, read only
# here since CPython would alias it; all-fresh operands copy nothing a
# program reaches, so that copy is silent.
def owned_elements_fn() -> None:
    a = P(1)
    b = P(2)
    # list comprehension: owned slot, a warned copy
    xs = [max(a, b, key=key_of) for _ in range(2)]  # tpyc: warning(/copies P into owned storage/)
    # tuple literal: owned slot, a warned copy
    t = (max(a, b, key=key_of), 1)  # tpyc: warning(/copies P into owned storage \(tuple element 0\)/)
    # all-fresh operands: the copy is unobservable
    f = [max(P(5), P(6), key=key_of)]  # tpyc: ok
    print("owned", xs[0].v, xs[1].v, t[0].v, t[1], f[0].v)


# a fresh operand makes the result a fresh value
def fresh_fn() -> None:
    a = P(1)
    # mixed: binding the result copies an operand the program may still
    # reach, so the binding says so
    t = min(a, P(0), key=key_of)  # tpyc: warning(/copies P/)
    t.v = 9
    print("fresh mixed", t.v, a.v)
    # an inline read of the same mixed call copies nothing
    print("fresh read", min(a, P(-9), key=key_of).v)  # tpyc: ok
    u = min(P(5), P(6), key=key_of)
    u.v += 1
    print("fresh all", u.v)


class W:
    p: P

    def __init__(self) -> None:
        self.p = P(7)

    def get(self) -> P:
        return self.p

    def ro(self) -> readonly[P]:
        return self.p


class Hp:
    _p: P

    def __init__(self) -> None:
        self._p = P(4)

    @property
    def p(self) -> P:
        return self._p


def tick(p: P) -> Iterator[int]:
    p.v += 1
    yield p.v


# operands whose form proves no storage: a method read off a temporary, a
# property getter -- a binding holds a copy and says so; a generator frame
# keeps its argument past the statement, so it holds a copy too (the
# temporary wins below, so CPython's write lands on an object nothing reads
# either: the line pins the warning, not the aliasing)
def unproven_fn() -> None:
    b = P(5)
    m = min(W().get(), b, key=key_of)  # tpyc: warning(/copies P into local 'm'/)
    h = Hp()
    n = min(h.p, b, key=key_of)  # tpyc: warning(/copies P into local 'n'/)
    print("unproven", m.v, n.v)
    a = P(1)
    print("frame", [v for v in tick(min(a, P(0), key=key_of))], a.v)  # tpyc: warning(/copies P into the frame of 'tick/)


# a container is a reference type too
def rows_fn() -> None:
    r1: list[int] = [1, 2, 3]
    r2: list[int] = [4, 5]
    longest = max(r1, r2, key=lambda r: len(r))
    longest.append(9)
    print("rows", r1, r2)


# native / template consumers bind the borrowed result in place
def consumers_fn() -> None:
    a = P(1)
    b = P(2)
    print("consumers", min(a, b, key=key_of), str(max(a, b, key=key_of)))
    r1: list[int] = [1, 2, 3]
    r2: list[int] = [4, 5]
    print("consumers len", len(max(r1, r2, key=lambda r: len(r))))


# value types are untouched
def values_fn() -> None:
    print("values", min(3, -4, key=lambda n: n * n), max(3, -4, key=lambda n: n * n))


# constructor arguments: the constructor writes the operand itself, also
# through a nested constructor
class Holder:
    seen: int

    def __init__(self, p: P) -> None:
        p.v += 1
        self.seen = p.v


class Outer:
    h: Holder

    def __init__(self, h: Own[Holder]) -> None:
        self.h = h


class Keeper:
    kept: P

    def __init__(self, p: P) -> None:
        # a constructor storing its parameter in a field keeps a copy, said
        # here (CPython aliases; the copy is the declared behaviour, so the
        # case does not write through `kept`)
        self.kept = p  # tpyc: warning(/copies P into field/)

    def write(self, p: P) -> int:
        p.v += 100
        return p.v

    def ret(self, p: P) -> P:
        p.v += 1
        return p


# a temporary operand at a WRITTEN constructor / method parameter: the call
# hands back a reference into the operands, valid for the statement, and
# the callee writes whichever object won -- the named one here
def mk_ps() -> Own[list[P]]:
    return [P(40), P(41)]


def head(xs: list[P]) -> P:
    return xs[0]


def temp_written_fn() -> None:
    a = P(1)
    h = Holder(min(a, P(5), key=key_of))
    print("temp ctor", h.seen, a.v)
    k = Keeper(a)
    print("temp method", k.write(max(a, P(-5), key=key_of)), a.v)
    ps = [P(3)]
    it = iter(ps)
    print("temp next", k.write(next(it, P(0))), ps[0].v)
    print("temp next default", k.write(next(it, P(0))))
    # a method handing the result back: the temporary operand is hoisted
    # into a local of the enclosing block, so the binding stays valid
    r = k.ret(min(a, P(-5), key=key_of))  # tpyc: warning(/Result borrows from temporary argument 'p'/)
    junk = [P(77), P(77), P(77)]
    print("temp method ret", r.v, a.v, len(junk))
    # a user borrow-returning call at the same method argument: its
    # temporary container hoists into a block local the result points into
    h2 = k.ret(head(mk_ps()))  # tpyc: warning(/Result borrows from temporary argument/)
    h2.v += 5
    junk2 = [P(78), P(78), P(78)]
    print("temp method head", h2.v, len(junk2))


def ctor_arg_fn() -> None:
    a = P(1)
    b = P(2)
    h = Holder(min(a, b, key=key_of))
    o = Outer(Holder(max(a, b, key=key_of)))
    print("ctor arg", h.seen, o.h.seen, a.v, b.v)
    # a mixed call in place: the callee writes whichever object won
    bump(min(a, P(0), key=key_of))
    bump(min(a, P(9), key=key_of))
    print("mixed arg", a.v)
    # an explicit copy is silent
    t = copy(min(a, P(0), key=key_of))
    t.v = 5
    print("copy", t.v, a.v)


class Ctx:
    def __enter__(self) -> None:
        pass

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


class Missing(Exception, ReturnException):
    pass


@error_return(Missing)
def lowest_or_raise(a: P, b: P) -> int:
    m = min(a, b, key=key_of)
    m.v += 10
    if m.v > 100:
        raise Missing
    return m.v


# with body, @error_return body and match arm
def positions_fn(tag: int) -> None:
    a = P(1)
    b = P(2)
    with Ctx():
        m = max(a, b, key=key_of)
        m.v = 20
    print("with", a.v, b.v)
    try:
        print("error_return", lowest_or_raise(a, b), a.v)
    except Missing:
        print("missing")
    match tag:
        case 1:
            k = min(a, b, key=key_of)
            k.v = 30
        case _:
            pass
    print("match", a.v, b.v)


# parameter operands written through an inline result: the call is a
# mutable use of them
def write_params(a: P, b: P) -> None:
    bump(max(a, b, key=key_of))
    min(a, b, key=key_of).v = 50


class Duo:
    a: P
    b: P

    def __init__(self) -> None:
        self.a = P(1)
        self.b = P(2)

    def bump_low(self) -> None:
        # field operands: the method takes `self` as mutable
        bump(min(self.a, self.b, key=key_of))


# a loop accumulator over a parameter aliases the winning element
def accumulate(ps: list[P]) -> int:
    best = ps[0]
    for p in ps:
        best = max(best, p, key=key_of)
    ps[1].v = 70
    return best.v


def params_fn() -> None:
    a = P(1)
    b = P(2)
    write_params(a, b)
    print("params", a.v, b.v)
    d = Duo()
    d.bump_low()
    print("params method", d.a.v, d.b.v)
    ps = [P(3), P(7), P(5)]
    print("params acc", accumulate(ps), [p.v for p in ps])


# the three-operand form in a method and a closure
class Trio:
    a: P
    b: P
    c: P

    def __init__(self) -> None:
        self.a = P(3)
        self.b = P(1)
        self.c = P(2)

    def lowest(self) -> P:
        return min(self.a, self.b, self.c, key=key_of)


def trio_fn() -> None:
    t = Trio()
    t.lowest().v = 40
    a = P(5)
    b = P(6)
    c = P(4)

    def inner() -> None:
        m = max(a, b, c, key=key_of)
        m.v = 50

    inner()
    print("trio", t.b.v, a.v, b.v, c.v)


def main() -> None:
    free_fn()
    a = P(1)
    b = P(2)
    print("read_only", read_only(a, b))
    r = pick_low(a, b)
    r.v = 60
    print("return", a.v, b.v)
    print("peek", peek_low(a, b).v)
    print("ro", ro_operands(a, b))
    print("ro temp", ro_temp(a))
    for x in gen_body(a, b):
        print("gen", x)
    print("gen after", a.v, b.v)
    print("async", asyncio.run(async_body(a, b)))
    print("async after", a.v, b.v)
    closure_fn()
    try_fn(True)
    comp_fn()
    owned_elements_fn()
    fresh_fn()
    rows_fn()
    consumers_fn()
    values_fn()
    ctor_arg_fn()
    positions_fn(1)
    trio_fn()
    params_fn()
    temp_written_fn()
    unproven_fn()
    t = Team(a, b)
    print("ctor", t.lead.v)
    low = t.low()
    low.v = -1
    print("method", t.a.v, t.b.v)


# module level: a global bound to the result is the operand itself
GA = P(1)
GB = P(2)
GM = min(GA, GB, key=key_of)
GM.v = 7
print("module", GA.v, GB.v)
main()
