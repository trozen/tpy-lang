# A local first bound to a record element of a tuple and then rebound
# (`a = t[0]; a = q`) re-points as the scalar `a = b; a = q` does, and `del`
# of a rebound pointer local never moves out of what another binding owns.
import asyncio
from typing import Iterator
from tpy import Own, int32, readonly


class Box:
    xs: list[int32]

    def __init__(self, n: int32) -> None:
        self.n = n
        self.xs = [n]

    def __iadd__(self, o: "Box") -> "Box":
        self.n += o.n
        return self

    def __enter__(self) -> "Box":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


class BoxErr(Exception):
    xs: list[int32]

    def __init__(self, n: int32) -> None:
        super().__init__()
        self.xs = [n]


class H:
    b: Box
    c: Box

    def __init__(self) -> None:
        self.b = Box(1)
        self.c = Box(2)

    def get_b(self) -> Box:
        return self.b

    def get_c(self) -> Box:
        return self.c

    # method: a borrow-tuple local's element, rebound to a parameter
    def rebind(self, q: Box) -> None:
        t = (self.b, 1)
        a = t[0]  # tpyc: ok
        a.n = 11
        a = q  # tpyc: ok
        a.n = 12
        print("method", self.b.n, q.n)


def first[T](xs: list[T]) -> T:
    return xs[0]


# free fn: borrow-tuple local
def tuple_local(b: Box, q: Box) -> None:
    t = (b, 1)
    a = t[0]  # tpyc: ok
    a.n = 3
    a = q  # tpyc: ok
    a.n = 4
    print("tuple_local", b.n, q.n, t[0].n)


# free fn: borrow-tuple parameter
def param_rebind(p: tuple[Box, int32], q: Box) -> None:
    a = p[0]  # tpyc: ok
    a.n = 5
    a = q  # tpyc: ok
    a.n = 6
    print("param", p[0].n, q.n)


# free fn: an element the tuple holds inline
def inline_elem(q: Box) -> None:
    w = (Box(2), 1)
    a = w[0]  # tpyc: ok
    a.n = 7
    a = q
    a.n = 8
    print("inline", w[0].n, q.n)


# free fn: an `Own` tuple parameter's element
def own_param(p: Own[tuple[Box, int32]], q: Box) -> None:
    a = p[0]  # tpyc: ok
    a.n = 9
    a = q
    a.n = 10
    print("own_param", p[0].n, q.n)


# free fn: the reseat takes another tuple's element
def elem_reseat(p: tuple[Box, int32], r: tuple[Box, int32]) -> None:
    a = p[0]
    a.n = 13
    a = r[0]  # tpyc: ok
    a.n = 14
    print("elem_reseat", p[0].n, r[0].n)


# free fn: readonly tuples, the slot a const pointer
def readonly_reseat(p: readonly[tuple[Box, int32]],
                    r: readonly[tuple[Box, int32]]) -> None:
    a = p[0]
    n0 = a.n
    a = r[0]  # tpyc: ok
    print("readonly", n0, a.n)


# closure: reads the name after the rebind (late binding)
def closure(b: Box, q: Box) -> None:
    t = (b, 1)
    a = t[0]
    a.n = 15

    def show() -> int32:
        return a.n
    a = q
    a.n = 16
    print("closure", b.n, q.n, show())


# match arm
def match_arm(k: int32, b: Box, q: Box) -> None:
    t = (b, 1)
    match k:
        case 1:
            a = t[0]  # tpyc: ok
            a.n = 17
            a = q
            a.n = 18
        case _:
            pass
    print("match", b.n, q.n)


# loop body (the name is not read after the loop:
# BUGS.md#loop-body-first-binding-read-after-loop-dangles)
def loop_body(p: tuple[Box, int32], q: Box) -> None:
    for i in range(2):
        a = p[0]  # tpyc: ok
        a.n += 1
        a = q
        a.n += 10
    print("loop", p[0].n, q.n)


# generator: the reseat source a local of the generator
def gen(p: tuple[Box, int32]) -> Iterator[int32]:
    a = p[0]
    a.n = 19
    yield a.n
    c = Box(30)
    a = c
    a.n += 1
    yield a.n


# try/finally body
def try_finally(p: tuple[Box, int32], q: Box) -> None:
    try:
        a = p[0]
        a.n = 20
        a = q
        a.n = 21
    finally:
        print("try_finally", p[0].n, q.n)


# del: an owned slot, then re-pointed at a parameter
def del_own_then_borrow(q: Box, c: bool) -> None:
    a = Box(1)
    if c:
        a = q
    del a  # tpyc: ok
    print("del_own_then_borrow", len(q.xs))


# del: an unpack target re-pointed
def del_unpack(q: Box) -> None:
    b = Box(1)
    t = (b, 1)
    a, k = t
    a = q
    del a
    print("del_unpack", len(b.xs), len(q.xs), k)


# del: born from a list element
def del_list_elem() -> None:
    ps = [Box(1), Box(2)]
    p = ps[0]
    p = ps[1]
    del p
    print("del_list_elem", len(ps[0].xs), len(ps[1].xs))


# del: born from a borrowing method call
def del_borrow_call(h: H) -> None:
    a = h.get_b()
    a = h.get_c()
    del a
    print("del_borrow_call", len(h.b.xs), len(h.c.xs))


# del: born from max / min, which hand back an operand
def del_max_min(x: Box, y: Box) -> None:
    a = max(x, y, key=lambda b: b.n)
    a = min(x, y, key=lambda b: b.n)
    del a
    print("del_max_min", len(x.xs), len(y.xs))


# del: born from a generic function returning an element
def del_generic(ps: list[Box], qs: list[Box]) -> None:
    a = first(ps)
    a = first(qs)
    del a
    print("del_generic", len(ps[0].xs), len(qs[0].xs))


# del: the rebound tuple element itself
def del_tuple_elem(p: tuple[Box, int32], q: Box) -> None:
    a = p[0]
    a = q
    del a
    print("del_tuple_elem", len(p[0].xs), len(q.xs))


# del: rebound from a nested def through nonlocal -- a parity section only: a
# nonlocal reference local is held by value today
# (BUGS.md#nonlocal-reference-rebind-copies), so it does not reach the
# pointer arm
def del_nonlocal(q: Box) -> None:
    a = Box(1)

    def g() -> None:
        nonlocal a
        a = Box(3)
    g()
    del a
    print("del_nonlocal", len(q.xs))


# del: every binding fresh -- the object is dropped at the del
def del_fresh_only(c: bool) -> None:
    a = Box(1)
    if c:
        a = Box(2)
    del a
    print("del_fresh_only done")


# del: a fresh-only local lent to another name first
def del_alias(c: bool) -> None:
    a = Box(1)
    if c:
        a = Box(2)
    b = a
    del a
    print("del_alias", len(b.xs), b.n)


# del: a fresh-only local kept in a list first
def del_keep(c: bool) -> None:
    a = Box(1)
    if c:
        a = Box(2)
    keep = [a]
    del a
    print("del_keep", len(keep[0].xs), keep[0].n)


# del inside a nested def: its own locals' bindings decide, not the
# enclosing body's -- a record local re-pointed at a captured name
def del_nested_record(b: Box) -> None:
    def inner() -> None:
        a = Box(9)
        a.n = 1
        a = b
        del a  # tpyc: ok
    inner()
    print("del_nested_record", len(b.xs))


# del inside a nested def: a rebound tuple element
def del_nested_elem(q: Box) -> None:
    def inner() -> None:
        t = (q, 1)
        a = t[0]
        a = q
        del a  # tpyc: ok
    inner()
    print("del_nested_elem", len(q.xs))


# del after binding targets other than an assignment: an augmented
# assignment, a `with` target over an owned slot, an `except` target and a
# walrus whose name is then re-pointed
def del_aug(q: Box) -> None:
    a = q
    a += Box(1)
    del a  # tpyc: ok
    print("del_targets aug", len(q.xs), q.n)


def del_with(m: Box) -> None:
    a = Box(1)
    a = Box(2)
    with m as a:  # tpyc: ok
        a.n += 1
    del a
    print("del_targets with", len(m.xs), m.n)


def del_except() -> None:
    n = 0
    try:
        raise BoxErr(4)
    except BoxErr as a:
        n = len(a.xs)
        del a  # tpyc: ok
    print("del_targets except", n)


def del_walrus(ps: list[Box]) -> None:
    if (a := Box(5)).n > 0:
        a = ps[0]
        del a  # tpyc: ok
    print("del_targets walrus", len(ps[0].xs))


# async: an element alias kept across an `await`, re-pointed at a local, then
# deleted
async def coro(p: tuple[Box, int32]) -> None:
    a = p[0]
    a.n = 4
    await asyncio.sleep(0)
    q = Box(9)
    a = q
    a.n = 11
    del a  # tpyc: ok
    print("async", p[0].n, len(p[0].xs), q.n, len(q.xs))


# generator: del of a frame pointer field that borrows -- a loop variable,
# an unpack target and a name alias
def gen_del(ps: list[Box], t: tuple[Box, int32]) -> Iterator[int32]:
    for a in ps:
        yield a.n
        del a  # tpyc: ok
    b, k = t
    yield k
    del b  # tpyc: ok
    w = Box(7)
    c = w
    yield c.n
    del c  # tpyc: ok
    yield len(w.xs)


# generator: del of an `except` target in a handler that suspends
def gen_except() -> Iterator[int32]:
    try:
        raise BoxErr(4)
    except BoxErr as a:
        yield len(a.xs)
        del a  # tpyc: ok
    yield 0


# an Optional local re-pointed at a container element, mutable and readonly
def opt_reseat(items: list[Box], ro: readonly[list[Box]]) -> None:
    saved: Box | None = None
    saved = items[0]  # tpyc: ok
    if saved is not None:
        saved.n = 40
    r: Box | None = None
    r = ro[0]  # tpyc: ok
    print("opt_reseat", items[0].n, r.n if r is not None else -1)


# inverse: a single binding of the element
def single(b: Box) -> None:
    t = (b, 1)
    a = t[0]  # tpyc: ok
    a.n = 22
    print("single", b.n)


# inverse: the scalar twin
def scalar_twin(b: Box, q: Box) -> None:
    a = b
    a.n = 23
    a = q  # tpyc: ok
    a.n = 24
    print("scalar_twin", b.n, q.n)


def main() -> None:
    h = H()
    # a named argument: BUGS.md#method-temp-arg-reseat-param-rejects
    q0 = Box(0)
    h.rebind(q0)
    tuple_local(Box(0), Box(0))
    b = Box(0)
    param_rebind((b, 1), Box(0))
    inline_elem(Box(0))
    own_param((Box(0), 1), Box(0))
    r = Box(0)
    elem_reseat((b, 1), (r, 2))
    readonly_reseat((b, 1), (r, 2))
    closure(Box(0), Box(0))
    match_arm(1, Box(0), Box(0))
    loop_body((b, 1), Box(0))
    print("gen", list(gen((b, 1))), b.n)
    try_finally((b, 1), Box(0))
    del_own_then_borrow(Box(2), True)
    del_unpack(Box(2))
    del_list_elem()
    del_borrow_call(H())
    del_max_min(Box(1), Box(2))
    del_generic([Box(1)], [Box(2)])
    del_tuple_elem((b, 1), Box(2))
    del_nonlocal(Box(2))
    del_fresh_only(True)
    del_alias(True)
    del_keep(True)
    del_nested_record(Box(2))
    del_nested_elem(Box(3))
    del_aug(Box(2))
    del_with(Box(3))
    del_except()
    del_walrus([Box(5)])
    asyncio.run(coro((b, 1)))
    ps = [Box(1), Box(2)]
    print("gen_del", list(gen_del(ps, (b, 6))), len(ps[0].xs), len(ps[1].xs),
          len(b.xs))
    print("gen_except", list(gen_except()))
    xs = [Box(1)]
    opt_reseat(xs, xs)
    single(Box(0))
    scalar_twin(Box(0), Box(0))


main()
