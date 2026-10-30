# A statement unpack target binds its element exactly as `a = t[i]` does:
# same ownership, loans, provenance and liveness, at every position and
# element shape. Each section writes through the target or reads it after
# the boundary, and prints the owner.
import asyncio
from typing import Iterator
from tpy import int32, Own, readonly, error_return, ReturnException, Span, Ptr, take_ptr
from tplib import Box, Rc


class P:
    data: list[int32]

    def __init__(self, n: int32) -> None:
        self.data = [n, n]


class Bad(Exception, ReturnException):
    pass


def size(p: P) -> int32:
    return len(p.data)


def opt_size(p: P | None) -> int32:
    if p is None:
        return -1
    return len(p.data)


def eat(t: Own[tuple[P, int32]]) -> int32:
    return t[1]


def mk(n: int32) -> Own[tuple[P, int32]]:
    return (P(n), n + 1)


def keep(p: Own[P]) -> Own[P]:
    return p


class M:
    p: P

    def __init__(self) -> None:
        self.p = P(1)


def pick(m: M) -> tuple[P, int32]:
    return (m.p, 3)


# free function: a later move of the source copies, as for `a = t[0]`
def move_after_unpack() -> None:
    t = (P(1), 2)
    a, k = t  # the subject: `a` refers to element 0 of `t`
    eat(t)  # tpyc: warning(/copies P into owned storage \(tuple element 0\)/)
    print("move_after", len(a.data), k)


# inverse: with the targets dead before the source's last use, the source
# still moves, as it does after `a = t[0]`
def move_after_dead_unpack() -> None:
    t = (P(1), 2)
    a, k = t
    print("dead_unpack", len(a.data), k)
    print("dead_unpack eat", eat(t))  # tpyc: ok


def move_after_unpack_param(t: Own[tuple[P, int32]]) -> None:
    a, k = t  # the subject: `a` refers to element 0 of the parameter
    eat(t)  # tpyc: warning(/copies P into owned storage \(tuple element 0\)/)
    print("move_after_param", len(a.data), k)


# rebound and branch-bound targets: the source's later move copies too
def move_after_rebound_unpack() -> None:
    a = P(0)
    k = 0
    t = (P(1), 2)
    a, k = t  # the subject: a rebound target refers to element 0 of `t`
    eat(t)  # tpyc: warning(/copies P into owned storage \(tuple element 0\)/)
    print("move_after_rebound", len(a.data), k)


def move_after_branch_unpack(flag: bool) -> None:
    t = (P(1), 2)
    u = (P(3), 4)
    a, k = t
    if flag:
        a, k = u  # the subject: bound in one branch only
    eat(u)  # tpyc: warning(/copies P into owned storage \(tuple element 0\)/)
    print("move_after_branch", len(a.data), k)


# method: the source is a field of self
class H:
    pair: tuple[P, int32]
    items: list[tuple[P, int32]]

    def __init__(self) -> None:
        self.pair = (P(1), 1)
        self.items = [(P(1), 1)]

    def write_through(self) -> None:
        a, k = self.pair  # the subject: `a` aliases self.pair[0]
        a.data.append(k)
        b, j = self.pair
        print("method", len(b.data), j)


# constructor: the target aliases an element of a parameter's field
class C:
    n: int32

    def __init__(self, h: H) -> None:
        a, k = h.pair  # the subject
        a.data.append(k)
        self.n = len(a.data)


# generator: the source's move after the unpack copies
def gen_move_unpack() -> Iterator[int32]:
    t = (P(1), 2)
    a, k = t  # the subject
    yield eat(t)  # tpyc: warning(/copies P into owned storage \(tuple element 0\)/)
    yield len(a.data)


# async: a write through the target reaches the caller's tuple
async def async_write(t: tuple[P, int32]) -> int32:
    a, k = t  # the subject
    await asyncio.sleep(0)
    a.data.append(k)
    return len(a.data)


# closure: the closure reads the target and the source after the unpack
def closure_reads(t: tuple[P, int32]) -> None:
    a, k = t  # the subject

    def go() -> None:
        print("closure", len(a.data), size(t[0]))

    a.data.append(k)
    go()


# match arm
def in_match(t: tuple[P, int32], sel: int32) -> None:
    match sel:
        case 1:
            a, k = t  # the subject
            a.data.append(k)
        case _:
            pass
    print("match", size(t[0]))


# @error_return body
@error_return(Bad)
def in_error_return(t: tuple[P, int32]) -> int32:
    a, k = t  # the subject
    a.data.append(k)
    if k < 0:
        raise Bad()
    return size(t[0])


# return off a parameter tuple: the target derives from the parameter, so
# it is returned by reference, as `a = t[0]` is
def ret_param_unpack(t: tuple[P, int32]) -> P:
    a, k = t  # the subject
    return a  # tpyc: ok


def ret_param_data_unpack(t: tuple[P, int32]) -> list[int32]:
    a, k = t  # the subject
    return a.data  # tpyc: ok


# swap of two records, then a write through one
def swap_records() -> None:
    x = P(1)
    y = P(2)
    x, y = y, x  # the subject  # tpyc: mir_borrowed(x) mir_borrowed(y) mir_borrows(x, y) mir_borrows(y, x)
    x.data.append(9)
    print("swap", len(x.data), len(y.data), x.data[0])


# reverse liveness: an element read before, two unpacks, a closure after
def reverse_liveness() -> None:
    t = (P(1), 2)
    u = t[0]
    a, k = t  # the subject
    a.data.append(k)
    b, j = t  # the subject: a second unpack of one source
    b.data.append(j)

    def go() -> int32:
        return size(t[0])

    print("reverse", len(u.data), go())


# str and BigInt targets with their source rebound after (and in a loop)
def value_targets() -> None:
    t = ("ab", 7)
    s, k = t  # the subject
    n: int = 10
    u = ("cd", n)
    w, m = u  # the subject: BigInt element
    t = ("xy", 8)
    u = ("zz", n + 1)
    print("value", s, k, w, m, t[0], u[1])
    out = ""
    v = ("p", 0)
    for i in range(3):
        q, r = v  # the subject: rebound each iteration
        out += q
        v = ("q", i)
    print("value_loop", out, v[0])


# Optional element
def optional_elem(t: tuple[P | None, int32]) -> None:
    a, k = t  # the subject
    if a is not None:
        a.data.append(k)
    print("optional", opt_size(t[0]))


# Own element: handed over at the source's last use
def own_elem(t: tuple[Own[P], int32]) -> None:
    a, k = t  # the subject: `t`'s last use, `a` owns element 0
    a.data.append(k)
    print("own", size(keep(a)))


# readonly element: borrowed, read only
def readonly_elem(xs: list[tuple[readonly[P], int32]]) -> None:
    a, k = xs[0]  # the subject
    print("readonly", len(a.data), k)


# Box / Rc element
def box_rc_elem() -> None:
    t = (Box(P(5)), Rc.new(P(6)))
    b, r = t  # the subject
    b.get().data.append(1)
    r.get().data.append(2)
    print("box_rc", size(t[0].get()), size(t[1].get()))


# Span element source: a write through a target read off the root span
# reaches the caller's list, so `xs` is a mutable parameter
class Cell:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Holder:
    pair: tuple[Cell, Cell]

    def __init__(self, a: int32, b: int32) -> None:
        self.pair = (Cell(a), Cell(b))


def span_slice_unpack(xs: list[Holder]) -> None:
    s: Span[Holder] = xs[0:1]
    a, b = s[0].pair  # the subject
    b.n += 5


# Span parameter source: the write reaches the caller's list through the
# root span the caller handed in
def span_param_unpack(s: Span[Holder]) -> None:
    a, b = s[0].pair  # the subject
    b.n += 10


# *args source: an element of the pack is the caller's object
def varargs_unpack(*hs: Holder) -> None:
    a, b = hs[0].pair  # the subject
    b.n += 100


# *args pack of spans: the pack's element is the caller's span, so a write
# through a target read off it reaches the list behind that span, as the
# declaration `h = ss[0][0]` does
def varargs_span_unpack(*ss: Span[Holder]) -> None:
    a, b = ss[0][0].pair  # the subject
    b.n += 1000


def varargs_span_decl(*ss: Span[Holder]) -> None:
    h = ss[0][0]
    h.pair[0].n += 1000


# each spelling through its own callee and caller, so neither keeps the
# other's list mutable
def varargs_span_caller(xs: list[Holder]) -> None:
    s: Span[Holder] = xs[0:1]
    varargs_span_unpack(s)


def varargs_span_decl_caller(xs: list[Holder]) -> None:
    s: Span[Holder] = xs[0:1]
    varargs_span_decl(s)


# *args pack of Ptr: a write through a Ptr FIELD of the pointee writes that
# field's referent, not the pointee, so the pack stays const in both spellings
class Gauge:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def bump(self, d: int32) -> None:
        self.n += d


class Dial:
    g: Ptr[Gauge]

    def __init__(self, g: Ptr[Gauge]) -> None:
        self.g = g


def ptr_pack_direct(*ds: Ptr[Dial]) -> None:
    ds[0].g.bump(1)


def ptr_pack_unpack(*ds: Ptr[Dial]) -> None:
    a, b = ds[0].g, 1  # the subject
    a.bump(b)


# never called: a grow while the target is live is undefined behavior
def grow_while_borrowed(h: H) -> None:
    a, k = h.items[0]
    h.items.append((P(9), 9))  # tpyc: warning(/Mutation of 'h.items' while borrowed/)
    print(len(a.data), k)


# never called: the temporary argument is hoisted to the end of the block,
# so the shape is safe; the warning is the filed false positive
# BUGS.md#lend-back-of-hoisted-temp-warned-as-dangling, as on its twin
def temp_arg() -> None:
    a, k = pick(M())  # tpyc: warning(/Result borrows from temporary argument/)
    print(len(a.data), k)


def main() -> None:
    move_after_unpack()
    move_after_dead_unpack()
    move_after_unpack_param(mk(3))
    move_after_rebound_unpack()
    move_after_branch_unpack(True)
    H().write_through()
    h = H()
    c = C(h)
    x, _j = h.pair
    print("ctor", c.n, len(x.data))
    print("generator", list(gen_move_unpack()))
    t2 = (P(1), 3)
    print("async", asyncio.run(async_write(t2)), size(t2[0]))
    closure_reads((P(1), 4))
    in_match((P(1), 5), 1)
    try:
        print("error_return", in_error_return((P(1), 6)))
    except Bad:
        print("error_return raised")
    t3 = (P(5), 1)
    r = ret_param_unpack(t3)
    r.data.append(1)
    print("ret_param", size(t3[0]))
    d = ret_param_data_unpack(t3)
    d.append(2)
    print("ret_param_data", size(t3[0]))
    swap_records()
    reverse_liveness()
    value_targets()
    optional_elem((P(1), 7))
    own_elem((P(4), 1))
    readonly_elem([(P(1), 8)])
    box_rc_elem()
    hs = [Holder(1, 2)]
    span_slice_unpack(hs)
    print("span", hs[0].pair[1].n)
    span_param_unpack(hs[0:1])
    print("span_param", hs[0].pair[1].n)
    hv = Holder(1, 2)
    varargs_unpack(hv)
    print("varargs", hv.pair[1].n)
    hw = [Holder(1, 2)]
    varargs_span_caller(hw)
    hd = [Holder(1, 2)]
    varargs_span_decl_caller(hd)
    print("varargs_span", hw[0].pair[1].n, hd[0].pair[0].n)
    gauge = Gauge(0)
    dial = Dial(take_ptr(gauge))
    ptr_pack_direct(take_ptr(dial))
    ptr_pack_unpack(take_ptr(dial))
    print("ptr_pack", gauge.n)


main()
