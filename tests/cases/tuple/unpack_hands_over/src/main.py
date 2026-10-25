# Each unpack target binds as `x = <element i>` would, at every statement,
# `for` head and comprehension clause position; one section per cell.
import asyncio
from typing import Iterator
from tpy import (Own, int32, nocopy, error_return, ReturnException, own_iter,
                 copy_iter, Array)


# A lent section mutates through its target, then reads the source: a target
# refers to the source's element unless the source hands that element over.
class A:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


@nocopy
class N:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class H:
    t: tuple[A, int32]
    a: A

    def __init__(self, v: int32) -> None:
        self.t = (A(v), v)
        self.a = A(v + 1)

    # method: a field source; the target refers into the field
    def bump(self) -> None:
        x, n = self.t  # tpyc: ok
        x.v += n
        print("method", self.t[0].v, x.v)

    # method, const source: a read-only method's field
    def peek(self) -> int32:
        x, n = self.t  # tpyc: ok
        return x.v + n


class C:
    seen: int32

    # constructor: a parameter source
    def __init__(self, t: tuple[A, int32]) -> None:
        x, n = t  # tpyc: ok
        x.v += 100
        self.seen = x.v + n


class E(Exception, ReturnException):
    pass


class Cm:
    def __enter__(self) -> int32:
        return 1

    def __exit__(self, et, ev, tb) -> None:
        pass


def mk_own(v: int32) -> tuple[Own[N], int32]:
    return (N(v), v)


def mk_whole(v: int32) -> Own[tuple[A, int32]]:
    return (A(v), v)


def mixed(h: H) -> tuple[Own[N], A]:
    return (N(7), h.a)


def lend(h: H) -> tuple[A, int32]:
    return (h.a, 3)


def mk_sb() -> tuple[str, bytes]:
    return ("str-owned", b"bytes")


def mk_two() -> tuple[int32, int32]:
    return (1, 2)


def pairs() -> Iterator[tuple[int32, str]]:
    yield (1, "one")
    yield (2, "two")


# free function, parameter source (lent): the target refers to the caller's A
def fn_param(t: tuple[A, int32]) -> None:
    x, n = t  # tpyc: ok
    x.v = 5
    print("fn_param", t[0].v, x.v, n)


# free function, local name source, beside its scalar twin `y = t[0]`
def fn_local() -> None:
    t = mk_whole(1)
    x, n = t  # tpyc: ok
    y = t[0]
    x.v = 9
    print("fn_local", t[0].v, y.v, n)


# free function, field source
def fn_field(h: H) -> None:
    x, n = h.t  # tpyc: ok
    x.v = 11
    print("fn_field", h.t[0].v, n)


# free function, list element source
def fn_subscript(ps: list[tuple[A, int32]]) -> None:
    x, n = ps[0]  # tpyc: ok
    x.v = 12
    print("fn_subscript", ps[0][0].v, n)


# free function, the index operand is a target: the source is read once
def fn_index_written(ps: list[tuple[int32, int32]]) -> None:
    i = 1
    i, v = ps[i]  # tpyc: ok
    print("fn_index_written", i, v)


class Node:
    v: int32
    kids: list[tuple["Node", int32]]

    def __init__(self, v: int32) -> None:
        self.v = v
        self.kids = []


# free function, a target writes the name its source reads: the source is
# held once, the reference target re-pointed off it
def fn_root_written(root: Node) -> None:
    h = root
    n = 0
    h, n = h.kids[0]  # tpyc: ok
    h.v += 100
    print("fn_root_written", h.v, n, root.kids[0][0].v)


# free function, calls: an `Own` element is handed over, a lent one refers
def fn_call(h: H) -> None:
    keep: list[N] = []
    x, n = mk_own(4)  # tpyc: ok
    keep.append(x)
    o, b = mixed(h)  # tpyc: ok
    b.v = 13
    keep.append(o)
    y, k = lend(h)  # tpyc: ok
    y.v += k
    print("fn_call", keep[0].v, keep[1].v, n, h.a.v)


# free function, select of two lvalue tuples
def fn_select(c: bool, t1: tuple[int32, str], t2: tuple[int32, str]) -> None:
    n, s = t1 if c else t2  # tpyc: ok
    print("fn_select", n, s)


# free function, `next` of an iterator
def fn_next() -> None:
    it = pairs()
    try:
        n, s = next(it)  # tpyc: ok
        print("fn_next", n, s)
    except StopIteration:
        print("fn_next empty")


# free function, walrus: the targets refer into the name it binds
def fn_walrus() -> None:
    x, n = (t := mk_whole(6))  # tpyc: ok
    x.v = 60
    print("fn_walrus", t[0].v, x.v, n)


# free function, Optional element
def fn_optional(t: tuple[A | None, int32]) -> None:
    o, n = t  # tpyc: ok
    if o is not None:
        o.v = 3
    print("fn_optional", n)


# free function, owned str / bytes buffers moved out of the call's holder
def fn_buffers() -> None:
    s, b = mk_sb()  # tpyc: ok
    print("fn_buffers", s, len(b))


# free function, the same target twice: it ends as the last element
def fn_twice() -> None:
    x, x = mk_two()  # tpyc: ok
    print("fn_twice", x)


# try / finally body: the targets are predeclared before the try (a record
# target there is refused, BUGS.md#unpack-target-try-hoist-rejects)
def try_body(t: tuple[int32, str]) -> None:
    try:
        n, s = t  # tpyc: ok
        print("try_body", n, s)
    finally:
        print("try_body finally")


# a branch-declared reference target read after the branch: the pointer the
# branches re-point
def branch_ref(c: bool, t1: tuple[A, int32], t2: tuple[A, int32]) -> None:
    if c:
        x, n = t1  # tpyc: ok
    else:
        x, n = t2  # tpyc: ok
    x.v = 27
    print("branch_ref", t1[0].v, t2[0].v, n)


# @error_return body
@error_return(E)
def er_body(t: tuple[A, int32]) -> int32:
    x, n = t  # tpyc: ok
    x.v = 22
    return n


# match arm body
def match_body(k: int32, t: tuple[A, int32]) -> None:
    match k:
        case 1:
            x, n = t  # tpyc: ok
            x.v = 23
        case _:
            pass
    print("match_body", t[0].v)


# with body: the targets are predeclared before the block (a record target
# there is refused, BUGS.md#unpack-target-try-hoist-rejects)
def with_body(t: tuple[int32, str]) -> None:
    with Cm() as w:
        n, s = t  # tpyc: ok
        print("with_body", n + w, s)


# nested def
def nested(t: tuple[A, int32]) -> None:
    def inner() -> int32:
        x, n = t  # tpyc: ok
        x.v = 25
        return n
    k = inner()
    print("nested", t[0].v, k)


# generator: targets read after a suspension
def gen_body(t: tuple[A, int32]) -> Iterator[int32]:
    x, n = t  # tpyc: ok
    yield n
    x.v = 26
    yield x.v


async def fo(v: int32) -> tuple[Own[A], int32]:
    return (A(v), v)


# async: an awaited result's `Own` element is handed over; a declaration
# of the whole result owns it the same
async def async_body() -> int32:
    keep: list[A] = []
    x, n = await fo(31)  # tpyc: ok
    keep.append(x)
    t = await fo(32)
    y, m = t  # tpyc: ok
    y.v += 1
    print("async_body", keep[0].v, n, t[0].v, m)
    return keep[0].v


# generator: an `Own[tuple]` call result's element moves into the target's
# frame storage, so it outlives the suspension
def gen_own() -> Iterator[int32]:
    a, k = mk_whole(1)  # tpyc: ok
    yield a.v
    filler = [A(9), A(9), A(9)]
    print("gen_own", a.v, k, len(filler))
    yield a.v


# generator: a rebound target under an alias; the alias keeps the first object
def gen_own_rebind() -> Iterator[int32]:
    a, k = mk_whole(1)  # tpyc: ok
    keep = a
    yield a.v
    a, k = mk_whole(2)  # tpyc: ok
    keep.v += 100
    yield a.v + keep.v + k


# async: the rebound-under-an-alias twin across awaits
async def async_own_rebind() -> int32:
    a, k = mk_whole(1)  # tpyc: ok
    keep = a
    await asyncio.sleep(0)
    a, k = mk_whole(2)  # tpyc: ok
    keep.v += 100
    await asyncio.sleep(0)
    return a.v + keep.v + k


def mk_str(base: str, n: int32) -> tuple[Own[str], int32]:
    # a tuple literal builds the `Own[str]` element in place
    return (base * n, n)  # tpyc: ok


def mk_str_literal() -> tuple[Own[str], int32]:
    # a string literal at the `Own[str]` element, as `return "x"` is
    return ("x", 1)  # tpyc: ok


# free function: an `Own[str]` element moves into the target
def fn_own_str() -> None:
    s, n = mk_str("ab", 2)  # tpyc: ok
    s += "!"
    print("fn_own_str", s, n)


# free function: the literal's `Own[str]` element moves into the target
def fn_own_str_literal() -> None:
    s, n = mk_str_literal()  # tpyc: ok
    s += "y"
    print("fn_own_str_literal", s, n)


def gen_whole_n() -> Iterator[Own[tuple[N, int32]]]:
    yield (N(1), 10)
    yield (N(2), 20)


def gen_elem() -> Iterator[tuple[Own[A], int32]]:
    yield (A(1), 10)
    yield (A(2), 20)


def gen_nested(n: int32) -> Iterator[tuple[int32, tuple[int32, Own[A]]]]:
    for i in range(n):
        yield (i, (i, A(i * 5)))


class StepIt:
    i: int32

    def __init__(self) -> None:
        self.i = 0

    def __iter__(self) -> "StepIt":
        return self

    def __next__(self) -> tuple[Own[A], int32]:
        self.i += 1
        if self.i > 2:
            raise StopIteration
        return (A(self.i), self.i * 10)


class AStepIt:
    i: int32

    def __init__(self) -> None:
        self.i = 0

    def __aiter__(self) -> "AStepIt":
        return self

    async def __anext__(self) -> tuple[Own[A], int32]:
        self.i += 1
        if self.i > 2:
            raise StopAsyncIteration
        return (A(self.i), self.i * 10)


# for head, plain fn, (1): a whole `Own[tuple]` step is taken apart, its
# @nocopy element moved into storage of its own
def head_whole() -> None:
    keep: list[N] = []
    for x, n in gen_whole_n():  # tpyc: ok
        x.v += n
        keep.append(x)
    print("head_whole", keep[0].v, keep[1].v)


# for head, plain fn, (4): the step's `Own` element binds off the step
def head_elem() -> None:
    keep: list[A] = []
    for x, n in gen_elem():  # tpyc: ok
        x.v += n
        keep.append(x)
    print("head_elem", keep[0].v, keep[1].v)


# for head, plain fn, (6): a lent list's `Own` element is the list's; the
# target refers to it
def head_lent(xs: list[tuple[Own[A], int32]]) -> None:
    for x, n in xs:  # tpyc: ok
        x.v = 9 + n
    print("head_lent", xs[0][0].v, xs[1][0].v)


# scalar loop, plain fn, (7): a lent `list[Own[A]]` element is referred to
def scalar_lent(ys: list[Own[A]]) -> None:
    for y in ys:  # tpyc: ok
        y.v += 3
    print("scalar_lent", ys[0].v, ys[1].v)


# scalar loop, plain fn, (7): a rebound variable points at the element
# until the rebind
def scalar_lent_rebind(ys: list[Own[A]]) -> None:
    for y in ys:  # tpyc: ok
        y.v += 1
        y = A(60)
        y.v += 1
    print("scalar_lent_rebind", ys[0].v, ys[1].v)


# the (7) sections over a `list[Own[A]]` this caller was handed: a live
# `list[A]` passed to them directly warns a false copy,
# BUGS.md#own-element-tuple-list-param-rejects
def scalar_lent_all(ys: list[Own[A]]) -> None:
    scalar_lent(ys)
    scalar_lent_rebind(ys)
    print("scalar_lent after", ys[0].v)


# for head, plain fn, (10): `own_iter` hands every element over
def head_own_iter() -> None:
    xs: list[tuple[N, int32]] = []
    xs.append((N(1), 10))
    xs.append((N(2), 20))
    keep: list[N] = []
    for x, n in own_iter(xs):  # tpyc: ok
        x.v += n
        keep.append(x)
    print("head_own_iter", keep[0].v, keep[1].v)


# for head, plain fn: a user `__next__` hands over what it declares `Own`
def head_user_next() -> None:
    keep: list[A] = []
    for x, n in StepIt():  # tpyc: ok
        x.v += n
        keep.append(x)
    print("head_user_next", keep[0].v, keep[1].v)


# for head, plain fn: `copy_iter` steps are copies, the source unchanged
def head_copy_iter(xs: list[tuple[A, int32]]) -> None:
    for x, n in copy_iter(xs):  # tpyc: ok
        x.v += n
    print("head_copy_iter", xs[0][0].v)


# for head, plain fn: `enumerate` / `zip` / `d.items()` over lent
# containers refer to their elements
def head_combinators(ys: list[A], ns: list[int32], d: dict[str, A]) -> None:
    for i, y in enumerate(ys):  # tpyc: ok
        y.v += i
    for y, n in zip(ys, ns):  # tpyc: ok
        y.v += n
    for k, a in d.items():  # tpyc: ok
        a.v += len(k)
    print("head_combinators", ys[0].v, ys[1].v, d["k"].v)


# for head, plain fn: an Optional element refers to its payload
def head_optional(xs: list[tuple[A | None, int32]]) -> None:
    for o, n in xs:  # tpyc: ok
        if o is not None:
            o.v += n
    for o2, n2 in xs:  # tpyc: ok
        if o2 is not None:
            print("head_optional", o2.v, n2)


# for head, plain fn: a literal list's record and container members
def head_literal() -> None:
    for name, r in [("a", A(1)), ("b", A(2))]:  # tpyc: ok
        r.v += 10
        print("head_literal", name, r.v)
    for ws, n in [([1, 2], 3), ([4], 5)]:  # tpyc: ok
        ws.append(7)
        print("head_literal", len(ws), n)


# for head, plain fn: a nested value tuple target binds the step's element
def head_nested() -> None:
    total = 0
    for i, pair in gen_nested(3):  # tpyc: ok
        total = total + pair[1].v + i
    print("head_nested", total)


# for head, generator body: the targets live across the suspension
def gen_head_elem() -> Iterator[int32]:
    for x, n in gen_elem():  # tpyc: ok
        yield x.v
        x.v += n
        yield x.v


# for head, generator body: `own_iter` hands every @nocopy element over
def gen_head_own_iter() -> Iterator[int32]:
    xs: list[tuple[N, int32]] = []
    xs.append((N(1), 10))
    xs.append((N(2), 20))
    for x, n in own_iter(xs):  # tpyc: ok
        yield x.v
        x.v += n
        yield x.v


# for head, generator body: a lent container's element is referred to
def gen_head_lent(xs: list[tuple[A, int32]]) -> Iterator[int32]:
    for x, n in xs:  # tpyc: ok
        yield n
        x.v += n


# for head, generator body: `enumerate` over a lent list
def gen_head_enumerate(ys: list[A]) -> Iterator[int32]:
    for i, y in enumerate(ys):  # tpyc: ok
        k = i
        yield k
        y.v += 100


async def tick() -> None:
    await asyncio.sleep(0)


# for head, async body: the step's `Own` element lives across the await
async def async_head_elem() -> int32:
    s = 0
    for x, n in gen_elem():  # tpyc: ok
        await tick()
        s += x.v + n
    return s


# async for: `__anext__` hands over what it declares `Own`
async def async_for_head() -> int32:
    keep: list[A] = []
    async for x, n in AStepIt():  # tpyc: ok
        x.v += n
        keep.append(x)
    return keep[0].v + keep[1].v


# for head over a user `__next__`, async body
async def async_head_user_next() -> int32:
    total = 0
    for k, b in StepIt():  # tpyc: ok
        await tick()
        total += k.v + b
    return total


# genexpr clause head: the step's elements, the step's `Own` one included;
# a lent list's element is referred to (a write reaches the list)
def genexpr_heads(xs: list[tuple[A, int32]]) -> None:
    print("genexpr_heads", sum(x.v + n for x, n in gen_elem()),  # tpyc: ok
          sum(bump(x, n) for x, n in xs), xs[0][0].v)  # tpyc: ok


def two_n() -> Iterator[Own[tuple[N, N]]]:
    yield (N(1), N(2))
    yield (N(3), N(4))


def bump(a: A, k: int32) -> int32:
    a.v += k
    return a.v


# comprehension clause, (2): a whole `Own[tuple]` step is taken apart, its
# @nocopy element moved into the result
def comp_whole() -> None:
    keep = [x for x, n in gen_whole_n()]  # tpyc: ok
    keep[0].v += 100
    print("comp_whole", keep[0].v, keep[1].v)
    sums = [x.v + n for x, n in gen_whole_n()]  # tpyc: ok
    print("comp_whole", sums[0], sums[1])


# comprehension clause, (3): the step's `Own` element moves into the result
def comp_elem() -> None:
    keep = [x for x, n in gen_elem()]  # tpyc: ok
    keep[0].v += n_of(keep)
    print("comp_elem", keep[0].v, keep[1].v)


def n_of(xs: list[A]) -> int32:
    return len(xs)


# comprehension clause, (5): a lent list's `Own` element is the list's; the
# target refers to it (a write reaches the list) and a sink storing it
# copies, warned
def comp_lent(xs: list[tuple[Own[A], int32]]) -> None:
    ns = [bump(x, n) for x, n in xs]  # tpyc: ok
    ws = [x for x, n in xs]  # tpyc: warning(/copies A into owned storage/)
    print("comp_lent", ns[0], xs[0][0].v, ws[1].v)


# comprehension clause, (8): a lent `list[Own[A]]` element is referred to
# (a write reaches the list); a sink storing it copies it once, warned
def comp_scalar_lent(ys: list[Own[A]]) -> None:
    ns = [bump(y, 1) for y in ys]  # tpyc: ok
    ws = [y for y in ys]  # tpyc: warning(/copies A into owned storage/)
    print("comp_scalar_lent", ns[0], ys[0].v, ws[1].v)


# comprehension clause: `x, x` binds the last element, moved once
def comp_twice() -> None:
    keep = [x for x, x in two_n()]  # tpyc: ok
    print("comp_twice", keep[0].v, keep[1].v)


# dict comprehension: the key reads its target before the value moves it
def comp_dict() -> None:
    d = {x.v: x for x, n in gen_elem()}  # tpyc: ok
    e = {n: x for x, n in gen_whole_n()}  # tpyc: ok
    print("comp_dict", d[1].v, d[2].v, e[10].v, e[20].v)


# set comprehension and a nested clause: the inner clause's element moves
def comp_nested() -> None:
    s = {n for x, n in gen_elem()}  # tpyc: ok
    keep = [x for k in range(2) for x, n in gen_elem()]  # tpyc: ok
    print("comp_nested", len(s), len(keep), keep[3].v)


# comprehension clause: `own_iter` hands every element over
def comp_own_iter() -> None:
    xs: list[tuple[N, int32]] = []
    xs.append((N(1), 10))
    xs.append((N(2), 20))
    keep = [x for x, n in own_iter(xs)]  # tpyc: ok
    print("comp_own_iter", keep[0].v, keep[1].v)


# comprehension clause: `enumerate` / `zip` / `d.items()` over lent
# containers refer to their elements
def comp_combinators(ys: list[A], ns: list[int32], d: dict[str, A]) -> None:
    a = [bump(y, i) for i, y in enumerate(ys)]  # tpyc: ok
    b = [bump(y, n) for y, n in zip(ys, ns)]  # tpyc: ok
    c = [bump(v, len(k)) for k, v in d.items()]  # tpyc: ok
    print("comp_combinators", a[1], b[1], c[0], ys[1].v, d["k"].v)


# comprehension clause: an Optional element refers to its payload
def comp_optional(xs: list[tuple[A | None, int32]]) -> None:
    ns = [opt_bump(o, n) for o, n in xs]  # tpyc: ok
    after = [opt_bump(o, 0) for o, n in xs]  # tpyc: ok
    print("comp_optional", ns[0], ns[1], after[0])


def opt_bump(o: A | None, k: int32) -> int32:
    if o is None:
        return -1
    o.v += k
    return o.v


# comprehension clause: str elements copy out of the step
def comp_values(ps: list[tuple[str, int32]]) -> None:
    ks = [k + "!" * n for k, n in ps]  # tpyc: ok
    print("comp_values", ks[0], ks[1])


# Array comprehension: the indexed element is the clause's step
def comp_array(arr: Array[tuple[A, int32], 2]) -> None:
    ns = [bump(x, n) for x, n in arr]  # tpyc: ok
    print("comp_array", ns[0], ns[1], arr[0][0].v)


def gen_scalar() -> Iterator[Own[A]]:
    yield A(1)
    yield A(2)


def gen_opt() -> Iterator[Own[tuple[A | None, int32]]]:
    yield (A(3), 1)
    yield (None, 2)


def grow(ws: list[int32]) -> int32:
    ws.append(0)
    return len(ws)


type Tree[T] = T | list[Tree[T]]


def tree_pair(seed: Tree[int32]) -> tuple[Tree[int32], int32]:
    return (seed, 3)


# comprehension over a local list built from literals: the clause reads the
# list's resolved element types; the lent `list` element is referred to
def comp_literal_local() -> None:
    pairs = [(1, [1]), (2, [2])]
    pairs.append((3, [3]))
    ns = [grow(v) + k for k, v in pairs]  # tpyc: ok
    d = {k: grow(v) for k, v in pairs}  # tpyc: ok
    # copied out of the literal's storage, as `[y for y in [A(2), A(4)]]` is
    g = [x for x, x2 in [(A(2), 1), (A(4), 2)]]  # tpyc: warning(/copies A into owned storage/)
    g[0].v += 1
    print("comp_literal_local", ns, d[1], pairs[0][1], g[0].v, g[1].v)


class B:
    items: list[int32]

    def __init__(self, v: int32) -> None:
        self.items = [v]


def gen_b() -> Iterator[tuple[Own[B], int32]]:
    yield B(1), 10
    yield B(2), 20


# for head over `enumerate(...)` / `zip(...)` of a lent list: the target
# refers to the list's element (a write reaches it, nothing moves out of
# it); over a generator handing its `Own` element over, the target owns it
# (a NAME bound to any of these is refused, as is one bound to `iter(...)`,
# BUGS.md#combinator-tuple-step-copies-record-element)
def head_call_iter(bs: list[B], ns: list[int32]) -> None:
    for i, b in enumerate(bs):  # tpyc: ok
        b.items.append(i)
    for b2, n in zip(bs, ns):  # tpyc: ok
        b2.items.append(n)
    keep: list[B] = []
    for b3, n3 in gen_b():  # tpyc: ok
        b3.items.append(n3)
        keep.append(b3)
    print("head_call_iter", bs[0].items, bs[1].items, keep[0].items,
          keep[1].items)


# for head over a combinator, a copying sink: storing the lent element
# copies it, warned (read only after: CPython would alias the stored one)
def head_call_iter_sink(bs: list[B]) -> None:
    keep: list[B] = []
    for i, b in enumerate(bs):
        keep.append(b)  # tpyc: warning(/copies B into owned storage/)
    print("head_call_iter_sink", bs[0].items, keep[0].items)


def lend_a(a: A) -> Iterator[tuple[A, int32]]:
    yield a, 10


# for head, generator body, over a protocol parameter: builds as on master;
# the target refers to the lent step's element (the write reaches the
# caller's A); fed a container iterator (`iter(xs)`) the step copies the
# stored tuple and the write lands on the copy,
# BUGS.md#combinator-tuple-step-copies-record-element
def gen_head_param(it: Iterator[tuple[A, int32]]) -> Iterator[int32]:
    for a, n in it:  # tpyc: ok
        a.v += n
        yield 1


# scalar for over a select of two owning generators: what both arms hand
# over, the step's `Own` value, is the loop variable's (moved, no copy);
# an unpacking head over a select is refused, as one over a name
def scalar_select(flag: bool) -> None:
    keep: list[A] = []
    for x in (gen_scalar() if flag else gen_scalar()):
        x.v += 10
        keep.append(x)  # tpyc: ok
    alias = keep[0]
    alias.v += 100
    print("scalar_select", keep[0].v, keep[1].v)


# comprehension clause and for head over an owned Optional step: the target
# holds the step's Optional element
def comp_owned_optional() -> None:
    xs = [a.v + n for a, n in gen_opt() if a is not None]  # tpyc: ok
    ys = [opt_bump(a, n) for a, n in gen_opt()]  # tpyc: ok
    zs: list[int32] = []
    for a, n in gen_opt():  # tpyc: ok
        if a is not None:
            a.v += n
            zs.append(a.v)
    print("comp_owned_optional", xs, ys, zs)


# walrus over a pointer tuple: the targets read the name it binds
def walrus_ptr(xs: list[tuple[A, int32]]) -> None:
    x, n = (t := xs[0])  # tpyc: ok
    x.v = 40
    print("walrus_ptr", t[0].v, xs[0][0].v, n)


# for head, open T element: one body for every instantiation, so the target
# neither aliases nor owns (no write through it is expressible)
def open_t[T](ps: list[tuple[T, int32]], first: T) -> int32:
    s = 0
    for x, n in ps:  # tpyc: ok
        s += n
    # storing the open-T parameter copies it at a reference instantiation
    keep = [first]  # tpyc: warning(/may copy T into owned storage/)
    return s + len(keep)


# recursive-wrapper element: the target binds the wrapper's alternative (a
# list of such tuples cannot be built for a for head,
# BUGS.md#wrapper-tuple-list-unbuildable)
def wrapper_elem(s: Tree[int32]) -> None:
    a, n = tree_pair(s)  # tpyc: ok
    print("wrapper_elem", n, a == s)


# for head, `enumerate` / `zip` over an owning source: the combinator's step
# hands nothing over; the target refers into the step
def combinators_owning() -> None:
    total = 0
    for i, s in enumerate(gen_elem()):  # tpyc: ok
        total += i + s[1] + s[0].v
    for y, k in zip(gen_elem(), [5, 6]):  # tpyc: ok
        total += y[0].v + k
    print("combinators_owning", total)


# for head, generator body: `d.items()`, an Optional element and
# `copy_iter`, each across a suspension (a protocol `Iterable[Own[tuple]]`
# source is refused, BUGS.md#own-tuple-iterable-unpack-head-rejects; the
# Optional element is read only: a write through it is lost,
# BUGS.md#frame-optional-tuple-step-copied)
def gen_head_cells(d: dict[str, A], opts: list[tuple[A | None, int32]],
                   qs: list[tuple[A, int32]]) -> Iterator[int32]:
    for k, a in d.items():  # tpyc: ok
        yield len(k)
        a.v += 100
    for o, n in opts:  # tpyc: ok
        yield n
        if o is not None:
            yield o.v
    for c, j in copy_iter(qs):  # tpyc: ok
        yield j
        c.v += 1000


# genexpr clause: `d.items()` over a lent dict refers to its elements; the
# Optional element's write is lost, so its source is not read after (the
# frame copies the step tuple, BUGS.md#frame-optional-tuple-step-copied);
# `copy_iter`, `own_iter` and protocol sources are refused,
# BUGS.md#genexpr-over-user-iterable, BUGS.md#genexpr-owned-source-rejects
def genexpr_cells(d: dict[str, A], opts: list[tuple[A | None, int32]]) -> None:
    a = sum(bump(v, len(k)) for k, v in d.items())  # tpyc: ok
    b = sum(opt_bump(o, n) for o, n in opts)  # tpyc: ok
    print("genexpr_cells", a, b, d["k"].v)


# comprehension clause, `x, x` over reference elements: the last element,
# referred to
def comp_twice_ref(ps: list[tuple[A, A]]) -> None:
    ws = [bump(x, 1) for x, x in ps]  # tpyc: ok
    print("comp_twice_ref", ws, ps[0][1].v, ps[0][0].v)


def main() -> None:
    a = A(1)
    fn_param((a, 2))
    print("fn_param after", a.v)
    fn_local()
    h = H(1)
    fn_field(h)
    ps = [(A(1), 1), (A(2), 2)]
    fn_subscript(ps)
    print("fn_subscript after", ps[0][0].v)
    fn_index_written([(5, 6), (7, 8)])
    root = Node(1)
    root.kids.append((Node(2), 7))
    fn_root_written(root)
    fn_call(h)
    fn_select(True, (1, "x"), (2, "y"))
    fn_select(False, (1, "x"), (2, "y"))
    fn_next()
    fn_walrus()
    o = A(1)
    fn_optional((o, 2))
    print("fn_optional after", o.v)
    fn_buffers()
    fn_twice()
    h.bump()
    print("method const", h.peek())
    c = C((a, 3))
    print("ctor", c.seen, a.v)
    try_body((1, "s"))
    branch_ref(False, (A(1), 1), (A(2), 2))
    try:
        print("er_body", er_body((a, 4)), a.v)
    except E:
        print("er_body raised")
    match_body(1, (a, 1))
    with_body((1, "w"))
    nested((a, 1))
    g = A(1)
    gl = list(gen_body((g, 2)))
    print("gen_body", gl, g.v)
    r = asyncio.run(async_body())
    print("async", r)
    go = list(gen_own())
    print("gen_own", go)
    print("gen_own_rebind", list(gen_own_rebind()))
    print("async_own_rebind", asyncio.run(async_own_rebind()))
    fn_own_str()
    fn_own_str_literal()
    head_whole()
    head_elem()
    xs = [(A(1), 10), (A(2), 20)]
    head_lent(xs)
    ls: list[A] = []
    ls.append(A(1))
    ls.append(A(2))
    scalar_lent_all(ls)
    ys: list[A] = []
    ys.append(A(1))
    ys.append(A(2))
    head_own_iter()
    head_user_next()
    qs: list[tuple[A, int32]] = []
    qs.append((A(1), 10))
    qs.append((A(2), 20))
    head_copy_iter(qs)
    d: dict[str, A] = {}
    d["k"] = A(1)
    head_combinators(ys, [5, 6], d)
    opts: list[tuple[A | None, int32]] = []
    opts.append((A(1), 4))
    head_optional(opts)
    head_literal()
    head_nested()
    print("gen_head_elem", list(gen_head_elem()))
    print("gen_head_own_iter", list(gen_head_own_iter()))
    print("gen_head_lent", list(gen_head_lent(qs)), qs[0][0].v)
    print("gen_head_enumerate", list(gen_head_enumerate(ys)), ys[0].v)
    print("async_head_elem", asyncio.run(async_head_elem()))
    print("async_for_head", asyncio.run(async_for_head()))
    print("async_head_user_next", asyncio.run(async_head_user_next()))
    genexpr_heads(qs)
    comp_whole()
    comp_elem()
    lx = [(A(1), 10), (A(2), 20)]
    comp_lent(lx)
    zs: list[A] = []
    zs.append(A(1))
    zs.append(A(2))
    comp_scalar_lent(zs)
    comp_twice()
    comp_dict()
    comp_nested()
    comp_own_iter()
    comp_combinators(ys, [5, 6], d)
    opts.append((None, 5))
    comp_optional(opts)
    comp_values([("p", 1), ("q", 2)])
    arr = [(A(1), 1), (A(2), 2)]
    comp_array(arr)
    print("comp_array after", arr[1][0].v)
    comp_literal_local()
    head_call_iter([B(1), B(2)], [5, 6])
    head_call_iter_sink([B(1), B(2)])
    pa = A(1)
    print("gen_head_param", list(gen_head_param(lend_a(pa))), pa.v)
    scalar_select(True)
    comp_owned_optional()
    wx = [(A(1), 2)]
    walrus_ptr(wx)
    print("open_t", open_t([(A(1), 1), (A(2), 2)], A(0)))
    tree: Tree[int32] = [1, 2]
    wrapper_elem(tree)
    combinators_owning()
    gd: dict[str, A] = {}
    gd["k"] = A(1)
    gq: list[tuple[A, int32]] = []
    gq.append((A(1), 10))
    print("gen_head_cells", list(gen_head_cells(gd, opts, gq)), gd["k"].v,
          gq[0][0].v)
    genexpr_cells(gd, opts)
    tp: list[tuple[A, A]] = []
    tp.append((A(1), A(2)))
    comp_twice_ref(tp)


main()
