# The C++ moves what a source's ownership record moves, silently, whatever
# form the binding takes; `A.__copy__` prints any copy the C++ makes.
import asyncio
from typing import Any, Callable, Iterable, Iterator, Self
from tpy import int32, Own, error_return, nocopy, own_iter, ReturnException


class A:
    items: list[int32]

    def __init__(self, n: int32) -> None:
        self.items = [n]

    def __copy__(self) -> Own[Self]:
        print("COPY", self.items[0])
        return A(self.items[0])


class F:
    # No printing copy hook: a generator frame binds an `Own` step by copy
    # (BUGS.md#frame-own-loop-var-copies-owned-step); the sink's move is
    # the subject.
    items: list[int32]

    def __init__(self, n: int32) -> None:
        self.items = [n]


class B:
    w: int32

    def __init__(self, w: int32) -> None:
        self.w = w


class Q:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


@nocopy
class N:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class EE(Exception, ReturnException):
    pass


class H:
    p: A
    f: F
    m: tuple[A, A]
    q: tuple[A, Q]
    u: A | B
    x: Any
    n: N
    o: A | None

    def __init__(self) -> None:
        self.p = A(0)
        self.f = F(0)
        self.m = (A(0), A(0))
        self.q = (A(0), Q(0))
        self.u = B(0)
        self.x = 0
        self.n = N(0)
        self.o = None

    def put_mixed(self, p: tuple[Own[A], Q]) -> None:
        # a mixed tuple parameter: its owned element 0 moves into the field
        # (the borrowed `Q` copies)
        self.q = p  # tpyc: warning(/copies Q into field \(tuple element 1\)/)

    def store(self, k: int32) -> None:
        try:
            a = make(k)
        except ValueError:
            a = A(7)
        # method: the hoisted local moves into the field
        self.p = a  # tpyc: ok


def make(k: int32) -> Own[A]:
    if k > 100:
        raise ValueError("x")
    return A(k)


# The parameter is only read: the warning is the callee's own, the move into
# it the subject of the sections that call it.
def take(a: Own[A]) -> int32:  # tpyc: warning(/never consumed/)
    a.items.append(5)
    return len(a.items)


def hoist_field(h: H, k: int32) -> None:
    try:
        a = make(k)
    except ValueError:
        a = A(7)
    # field: the hoisted local moves
    h.p = a  # tpyc: ok


def hoist_append(out: list[A], k: int32) -> None:
    try:
        a = make(k)
    except ValueError:
        a = A(7)
    # append: the hoisted local moves
    out.append(a)  # tpyc: ok


def hoist_own_arg(k: int32) -> int32:
    try:
        a = make(k)
    except ValueError:
        a = A(7)
    # Own argument: the hoisted local moves
    return take(a)  # tpyc: ok


def hoist_return(k: int32) -> Own[A]:
    try:
        a = make(k)
    except ValueError:
        a = A(7)
    # return: the hoisted local moves
    return a  # tpyc: ok


def hoist_gen(h: H, k: int32) -> Iterator[int32]:
    try:
        a = make(k)
    except ValueError:
        a = A(7)
    yield 1
    # generator frame: the hoisted local moves
    h.p = a  # tpyc: ok


async def hoist_async(h: H, k: int32) -> Own[A]:
    try:
        a = make(k)
    except ValueError:
        a = A(7)
    b = make(k + 1)
    await asyncio.sleep(0)
    # async frame: the hoisted local moves into the field and the result
    h.p = a  # tpyc: ok
    return b


async def hoist_async_return(k: int32) -> Own[A]:
    try:
        a = make(k)
    except ValueError:
        a = A(7)
    await asyncio.sleep(0)
    # async return: the hoisted local moves out of the frame
    return a  # tpyc: ok


@error_return(EE)
def hoist_error_return(h: H, k: int32) -> None:
    if k < 0:
        raise EE()
    try:
        a = make(k)
    except ValueError:
        a = A(7)
    # @error_return body: the hoisted local moves
    h.p = a  # tpyc: ok


def hoist_nested(h: H, k: int32) -> None:
    def inner() -> None:
        try:
            a = make(k)
        except ValueError:
            a = A(7)
        # nested def: the hoisted local moves
        h.p = a  # tpyc: ok
    inner()


def gen_consume(h: H, ys: list[A]) -> Iterator[int32]:
    xs: list[A] = []
    xs.append(A(1))
    xs.append(A(2))
    for a in xs:
        yield a.items[0]
        # generator consuming loop: the variable moves into the list / field
        if a.items[0] > 1:
            ys.append(a)
        else:
            h.p = a  # tpyc: ok


def mk() -> Iterator[Own[A]]:
    yield A(3)
    yield A(4)


def mkf() -> Iterator[Own[F]]:
    yield F(3)
    yield F(4)


def gen_own_elem(h: H, fs: list[F]) -> Iterator[int32]:
    for a in mkf():
        yield a.items[0]
        # generator over `Own` elements: the step moves
        if a.items[0] > 3:
            fs.append(a)  # tpyc: ok
        else:
            h.f = a  # tpyc: ok


def gen_own_iter(h: H, fs: list[F]) -> Iterator[int32]:
    src: list[F] = [F(5), F(6)]
    for a in own_iter(src):
        yield a.items[0]
        # generator `own_iter` loop: the step moves
        if a.items[0] > 5:
            fs.append(a)  # tpyc: ok
        else:
            h.f = a  # tpyc: ok


def loop_rebind(xs: Iterator[Own[A]], b: A, out: list[A]) -> None:
    for a in xs:
        a = A(4)
        # rebound `Own` loop variable: the rebind's object moves
        out.append(a)  # tpyc: ok
    a = b
    print("loop_rebind after", a.items[0])


def joined_fresh(h: H, c: bool) -> None:
    if c:
        ft = (A(1), A(2))
    else:
        ft = (A(3), A(4))
    # joined pointer tuple: both elements move
    h.m = ft  # tpyc: ok


def hoisted_mixed(h: H, q: Q, k: int32) -> None:
    try:
        m = (make(k), q)
    except ValueError:
        m = (A(7), q)
    # hoisted mixed tuple: the owned element moves, the borrowed one copies
    h.q = m  # tpyc: warning(/copies Q into field \(tuple element 1\)/)


def hoisted_finally(k: int32) -> Own[A]:
    try:
        a = make(k)
    except ValueError:
        a = A(7)
    try:
        # return under a finally: the move follows the finally's write
        return a  # tpyc: ok
    finally:
        a.items.append(9)


async def hoisted_finally_async(k: int32) -> Own[A]:
    try:
        a = make(k)
    except ValueError:
        a = A(7)
    try:
        await asyncio.sleep(0)
        # async return under a finally: the move follows the finally's write
        return a  # tpyc: ok
    finally:
        a.items.append(9)


def member_lift() -> tuple[tuple[Own[A], int32], int32]:
    t = (A(1), 2)
    # a tuple name member at its last use moves
    return (t, 3)  # tpyc: ok


def nested_member(xs: list[tuple[tuple[A, int32], int32]]) -> None:
    a = A(2)
    # a nested scalar member at its last use moves
    xs.append(((a, 1), 2))  # tpyc: ok


def comp_clause(csrc: Iterable[Own[tuple[A, A]]]) -> Own[list[tuple[A, A]]]:
    # a comprehension clause's owned tuple variable moves into the list
    return [ct for ct in csrc]  # tpyc: ok


def union_field(h: H, c: bool) -> None:
    another: A | B = A(2)
    if c:
        another = B(3)
    # union pointer variant: the pointee the local owns moves
    h.u = another  # tpyc: ok


def any_field(h: H) -> None:
    n = A(4)
    # `Any` field: the local moves into the box
    h.x = n  # tpyc: ok


def any_return(n: int32) -> Any:
    a = A(n)
    # `Any` result: the owned local moves into the box
    return a  # tpyc: ok


def consume_field(h: H) -> int32:
    xs: list[A] = []
    xs.append(A(18))
    xs.append(A(19))
    ys: list[A] = []
    for a in xs:
        if a.items[0] > 18:
            ys.append(a)
        else:
            # consuming loop: the variable moves into the field
            h.p = a  # tpyc: ok
    return len(ys)


def try_finally_store(h: H, k: int32) -> None:
    try:
        x = make(k)
    finally:
        print("try_finally_store finally")
    # a `try`-hoisted owning local moves into the field
    h.p = x  # tpyc: ok


def try_finally_nocopy(h: H, k: int32) -> None:
    try:
        x = N(k)
    finally:
        print("try_finally_nocopy finally")
    # ... a `@nocopy` one too: no copy is made, so nothing is refused
    h.n = x  # tpyc: ok


def nocopy_member(ns: list[tuple[int32, N]]) -> None:
    n = N(24)
    # a `@nocopy` literal member at its last use moves into the list
    ns.append((1, n))  # tpyc: ok


def len_of(a: A) -> int32:
    return len(a.items)


def walrus_gen(h: H, k: int32) -> Iterator[int32]:
    yield len_of(w := A(k))
    # generator frame: a walrus-bound local moves into the field
    h.p = w  # tpyc: ok


def make_opt(k: int32) -> Own[A] | None:
    if k > 0:
        return A(k)
    return None


def opt_record(h: H, k: int32) -> None:
    r = make_opt(k)
    # a local off an `Own[A] | None` call moves into the field
    h.o = r  # tpyc: ok


def own_pairs(n: int32) -> Iterator[tuple[int32, Own[F]]]:
    yield (n, F(n))


# Read only, as `take` is: its warning is the callee's own.
def take_f(f: Own[F]) -> int32:  # tpyc: warning(/never consumed/)
    f.items.append(5)
    return len(f.items)


def head_own(k: int32) -> int32:
    total = 0
    for i, b in own_pairs(k):
        # a for-unpack `Own` target moves into the `Own` parameter (`F`:
        # the target refers to its element in the step, which the move takes)
        total += take_f(b)  # tpyc: ok
    return total


def nocopy_list() -> int32:
    n = N(31)
    # a `@nocopy` member of a list literal at its last use moves in
    ns: list[tuple[int32, N]] = [(1, n)]  # tpyc: ok
    return ns[0][1].v


def mk_pair(k: int32) -> tuple[Own[A], int32]:
    return (A(k), 1)


def live_unpack(k: int32) -> None:
    t = mk_pair(k)
    # an unpack of a tuple name read again after it binds its targets to
    # the elements (no move, no copy), as the scalar `a = t` binds
    a, n = t  # tpyc: ok
    a.items.append(9)
    print("live_unpack", len(t[0].items), len(a.items), n)


def show(tag: str, a: A) -> None:
    print(tag, a.items[0], len(a.items))


class NBox:
    n: N

    def __init__(self, v: int32) -> None:
        self.n = N(v)


def mk_nbox(v: int32) -> Own[NBox]:
    return NBox(v)


def nocopy_temp_member(nb: NBox) -> None:
    # a `@nocopy` member of a fresh call's result moves out of the temporary
    nb.n = mk_nbox(41).n  # tpyc: ok


class AW:
    a: A

    def __init__(self, a: Own[A]) -> None:
        self.a = a


def mk_aw(n: int32) -> Own[AW]:
    return AW(A(n))


def own_callable_member(h: H) -> None:
    # a member of an `Own` result through a `Callable` value moves out of
    # the temporary
    fn: Callable[[int32], Own[AW]] = mk_aw
    h.p = fn(42).a  # tpyc: ok


def main() -> None:
    h = H()
    hoist_field(h, 1)
    h.p.items.append(9)
    show("hoist_field", h.p)
    out: list[A] = []
    hoist_append(out, 2)
    out[0].items.append(9)
    show("hoist_append", out[0])
    print("hoist_own_arg", hoist_own_arg(3))
    r = hoist_return(4)
    r.items.append(9)
    show("hoist_return", r)
    h.store(5)
    show("method", h.p)
    for v in hoist_gen(h, 6):
        print("gen yield", v)
    show("gen", h.p)
    b = asyncio.run(hoist_async(h, 7))
    show("async", h.p)
    show("async result", b)
    show("async_return", asyncio.run(hoist_async_return(8)))
    try:
        hoist_error_return(h, 9)
    except EE:
        pass
    show("error_return", h.p)
    hoist_nested(h, 10)
    show("nested_def", h.p)
    ys: list[A] = []
    for v in gen_consume(h, ys):
        print("consume yield", v)
    show("consume", h.p)
    print("consume appended", len(ys))
    fs: list[F] = []
    for v in gen_own_elem(h, fs):
        print("own_elem yield", v)
    print("own_elem", h.f.items[0], len(fs))
    for v in gen_own_iter(h, fs):
        print("own_iter yield", v)
    print("own_iter", h.f.items[0], len(fs))
    loop_rebind(mk(), A(11), ys)
    show("loop_rebind", ys[-1])
    joined_fresh(h, True)
    m0, m1 = h.m
    show("joined_fresh 0", m0)
    show("joined_fresh 1", m1)
    q = Q(12)
    hoisted_mixed(h, q, 13)
    q0, q1 = h.q
    show("hoisted_mixed 0", q0)
    print("hoisted_mixed 1", q1.v)
    show("hoisted_finally", hoisted_finally(14))
    show("hoisted_finally_async", asyncio.run(hoisted_finally_async(15)))
    # a copy of the member would print
    lifted = member_lift()
    print("member_lift", lifted[1])
    nested: list[tuple[tuple[A, int32], int32]] = []
    nested_member(nested)
    print("nested_member", len(nested))
    pairs: list[tuple[A, A]] = []
    pairs.append((A(16), A(17)))
    moved = comp_clause(own_iter(pairs))
    c0, c1 = moved[0]
    show("comp_clause 0", c0)
    show("comp_clause 1", c1)
    union_field(h, False)
    u = h.u
    if isinstance(u, A):
        show("union_field", u)
    # a copy into the box would print
    any_field(h)
    print("any_field done")
    any_return(28)
    print("any_return done")
    print("consume_field", consume_field(h))
    h.p.items.append(9)
    show("consume_field", h.p)
    try_finally_store(h, 20)
    h.p.items.append(9)
    show("try_finally_store", h.p)
    try_finally_nocopy(h, 21)
    h.n.v += 1
    print("try_finally_nocopy", h.n.v)
    q23 = Q(23)
    h.put_mixed((A(22), q23))
    p0, p1 = h.q
    p0.items.append(9)
    show("put_mixed", p0)
    ns: list[tuple[int32, N]] = []
    nocopy_member(ns)
    nk, nn = ns[0]
    print("nocopy_member", nk, nn.v)
    for v in walrus_gen(h, 25):
        print("walrus yield", v)
    h.p.items.append(9)
    show("walrus_gen", h.p)
    opt_record(h, 26)
    ho = h.o
    if ho is not None:
        ho.items.append(9)
        show("opt_record", ho)
    print("head_own", head_own(27))
    print("nocopy_list", nocopy_list())
    live_unpack(28)
    nb = NBox(40)
    nocopy_temp_member(nb)
    print("nocopy_temp_member", nb.n.v)
    own_callable_member(h)
    h.p.items.append(9)
    show("own_callable_member", h.p)


main()
