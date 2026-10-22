# A rebound tuple local of a generator or async body that owns an element is a
# pointer tuple, as in a plain function body: each owned element's object takes
# a frame field of its own per binding site, so an alias of the old tuple or of
# its element keeps the old object across the rebind and every suspension.
# A container store copies the tuple's elements (warned), as from a plain body;
# a last-use field store moves the owned element and copies the borrowed one
# (warned); those two sections only read the value back. The `_ints`, `_in_loop`, `_range_len` and `_holder` sections
# bind holders the storage verdict sees or that hold no pointer: ints, a tuple
# of ints, a `str` or value-type copy, a loop's iterator whose loan the borrow
# tracker records (plain function twins among them). The `_untracked`,
# `_retain`, `_setter_retain` and `_list_of_tuples_alias` sections bind holders
# by paths no tracked loan records; the rebind's own field keeps the old object.
import asyncio
from typing import Iterator

from tpy import Own, Ptr, ValueType, int32, nocopy, readonly, take_ptr


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


@nocopy
class U:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class H:
    bp: tuple[Box, Box]

    def __init__(self, a: Box, b: Box) -> None:
        self.bp = (a, b)  # tpyc: warning(/copies Box into field \(tuple element 0\)/) warning(/copies Box into field \(tuple element 1\)/)

    # generator method: the pointer tuple re-points at its receiver's field
    def walk(self, v: Box) -> Iterator[int32]:
        p = make_mixed(1, v)
        yield p[0].n
        p = self.bp  # tpyc: ok
        p[0].n += 100
        yield self.bp[0].n


class Ctx:
    def __enter__(self) -> int32:
        return 0

    def __exit__(self, et, ev, tb) -> None:
        pass


class G:
    xs: list[Box]

    def __init__(self, i: int32) -> None:
        self.xs = [Box(i), Box(i + 1)]


class Pt(ValueType):
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y


class PV[T](ValueType):
    a: T

    def __init__(self, a: Own[T]) -> None:
        self.a = a


class Named:
    s: str
    pv: PV[int32]

    def __init__(self, i: int32) -> None:
        self.s = "name" + str(i)
        self.pv = PV(i)


class N:
    ns: list[int32]
    ps: list[tuple[int32, int32]]
    pt: Pt

    def __init__(self, i: int32) -> None:
        self.ns = [i, i + 1]
        self.ps = [(i, i + 1)]
        self.pt = Pt(i, i + 1)


def make_mixed(n: int32, b: Box) -> tuple[Own[Box], Box]:
    return (Box(n), b)


def make_owned(n: int32) -> tuple[Own[Box], Own[Box]]:
    return (Box(n), Box(n + 1))


def make_pair() -> Own[tuple[Box, int32]]:
    return (Box(7), 3)


def first(g: G) -> Box:
    return g.xs[0]


async def mkbox(n: int32) -> Own[Box]:
    return Box(n)


def read(p: tuple[Box, Box]) -> int32:
    p[0].n += 100
    return p[0].n


async def bump_later(b: Box) -> int32:
    await asyncio.sleep(0)
    b.n += 100
    return b.n


# whole alias after two rebinds: `q` shares `p`'s element objects
def gen_alias_after_rebinds(v: Box) -> Iterator[int32]:
    p = make_mixed(1, v)
    p = make_mixed(2, v)
    q = p  # tpyc: ok
    q[0].n += 100
    yield p[0].n
    yield q[0].n


async def co_alias_after_rebinds(v: Box) -> int32:
    p = make_mixed(1, v)
    p = make_mixed(2, v)
    q = p  # tpyc: ok
    await asyncio.sleep(0)
    q[0].n += 100
    return p[0].n * 1000 + q[0].n


# rebind while a whole alias is live: the new object takes a field of its own
def gen_rebind_live_whole(v: Box, w: Box) -> Iterator[int32]:
    p = make_mixed(1, v)
    u = p
    yield u[0].n
    p = make_mixed(9, w)  # tpyc: ok
    u[0].n += 100
    yield u[0].n
    yield p[0].n


# element alias, then a rebind under a branch
def gen_rebind_live_elem(v: Box, c: bool) -> Iterator[int32]:
    p = make_mixed(1, v)
    a = p[0]  # tpyc: ok
    yield a.n
    if c:
        p = make_mixed(9, v)  # tpyc: ok
    a.n += 100
    yield a.n
    yield p[0].n


async def co_rebind_live_elem(v: Box, c: bool) -> int32:
    p = make_mixed(1, v)
    a = p[0]  # tpyc: ok
    await asyncio.sleep(0)
    if c:
        p = make_mixed(9, v)
    a.n += 100
    return a.n * 1000 + p[0].n


# an all-owned literal's element alias
def gen_literal_elem_alias(c: bool) -> Iterator[int32]:
    t = (Box(1), Box(2))
    saved = t[1]  # tpyc: ok
    yield saved.n
    if c:
        t = (Box(9), Box(8))
    saved.n += 100
    yield saved.n
    yield t[1].n


# the owned element of a mixed literal, aliased across an await
async def co_mixed_elem_alias(b: Box, c: bool) -> int32:
    t = (Box(1), b)
    saved = t[0]  # tpyc: ok
    await asyncio.sleep(0)
    if c:
        t = (Box(9), b)
    saved.n += 100
    return saved.n * 1000 + t[0].n


# a @nocopy element: its object is moved into the site's field, never copied
def gen_nocopy_elem_alias(c: bool) -> Iterator[int32]:
    t = (U(1), U(2))
    saved = t[1]  # tpyc: ok
    yield saved.x
    if c:
        t = (U(9), U(8))
    saved.x += 100
    yield saved.x
    yield t[1].x


# a borrowing binding first, an owning one after it
def gen_borrowed_then_owned(a: Box, c: bool) -> Iterator[int32]:
    t = (a, 2)
    if c:
        t = (Box(9), 3)  # tpyc: ok
    yield t[0].n
    t[0].n += 100
    yield a.n


# a borrowing literal and an owning call in exclusive branches
def gen_call_borrow_branches(a: Box, c: bool) -> Iterator[int32]:
    if c:
        t = (a, 2)
    else:
        t = make_pair()  # tpyc: ok
    yield t[0].n
    a.n = 99
    yield t[0].n


# an alias taken before a loop that rebinds on every pass
def gen_loop_rebind(v: Box) -> Iterator[int32]:
    p = make_mixed(1, v)
    q = p
    for i in range(2):
        p = make_mixed(10 + i, v)  # tpyc: ok
        yield p[0].n
    q[0].n += 100
    yield q[0].n


# a rebind in a match arm
def gen_match_arm(v: Box, w: Box, c: bool) -> Iterator[int32]:
    p = make_mixed(1, v)
    q = p
    match c:
        case True:
            p = make_mixed(5, w)  # tpyc: ok
        case _:
            pass
    q[0].n += 100
    yield q[0].n
    yield p[0].n


# a rebind in a finally
def gen_try_finally(v: Box, w: Box) -> Iterator[int32]:
    p = make_mixed(1, v)
    q = p
    try:
        yield q[0].n
    finally:
        p = make_mixed(6, w)  # tpyc: ok
    q[0].n += 100
    yield q[0].n
    yield p[0].n


# first bindings in both arms of an if
def gen_block_first(v: Box, c: bool) -> Iterator[int32]:
    if c:
        p = make_mixed(1, v)
    else:
        p = make_mixed(5, v)
    q = p
    p = make_mixed(2, v)  # tpyc: ok
    q[0].n += 100
    yield q[0].n
    yield p[0].n


# both elements owned
def gen_owned_owned() -> Iterator[int32]:
    p = make_owned(1)
    q = p
    p = make_owned(5)  # tpyc: ok
    q[1].n += 100
    yield q[1].n
    yield p[1].n


# re-point at a parameter's field
def gen_repoint_param_field(v: Box, h: H) -> Iterator[int32]:
    p = make_mixed(1, v)
    yield p[0].n
    p = h.bp  # tpyc: ok
    p[0].n += 100
    yield h.bp[0].n


# an alias bound by a call that lends from the element
def gen_call_alias(c: bool) -> Iterator[int32]:
    t = (G(1), 5)
    saved = first(t[0])  # tpyc: ok
    yield saved.n
    if c:
        t = (G(10), 6)
    saved.n += 100
    yield saved.n
    yield t[0].xs[0].n


# a live generator expression reads the creator's current tuple
def gen_genexpr_live() -> Iterator[int32]:
    p = (Box(1),)
    for x in (p[0].n for _ in range(2)):
        yield x
        p = (Box(2),)  # tpyc: ok
        p[0].n += 100


# a readonly element: every later binding gives its object a field of its own
def gen_readonly_elem(v: Box) -> Iterator[int32]:
    p: tuple[readonly[Box], Box] = (Box(1), v)
    q = p
    yield q[0].n
    p = (Box(2), v)  # tpyc: ok
    yield q[0].n
    yield p[0].n
    p[1].n += 100
    yield v.n


# unpacking the pointer tuple aliases its elements
def gen_unpack_after(v: Box) -> Iterator[int32]:
    p = make_mixed(1, v)
    p = make_mixed(2, v)
    x, y = p  # tpyc: ok
    x.n += 100
    yield p[0].n
    yield y.n


# an awaited object as a literal's owned element
async def co_await_element(v: Box) -> int32:
    p = (await mkbox(1), v)
    p = (await mkbox(2), v)  # tpyc: ok
    q = p
    q[0].n += 100
    return p[0].n * 1000 + q[0].n


# two hops through a BORROWED element point outside the tuple
def gen_two_hops_borrowed(h: H, c: bool) -> Iterator[int32]:
    p = (Box(1), h)
    a = p[1].bp[1]  # tpyc: ok
    yield a.n
    if c:
        p = (Box(8), h)
    a.n += 100
    yield h.bp[1].n


# two hops into an owned element, through a name of its own
def gen_two_step(c: bool) -> Iterator[int32]:
    t = (G(1), 5)
    inner = t[0]
    saved = inner.xs[1]  # tpyc: ok
    yield saved.n
    if c:
        t = (G(10), 6)
    saved.n += 100
    yield saved.n
    yield t[0].xs[1].n


# all elements borrowed: no element takes a field
def gen_all_borrow(v: Box, w: Box) -> Iterator[int32]:
    p = (v, 1)
    yield p[0].n
    p = (w, 2)  # tpyc: ok
    p[0].n += 100
    yield w.n


# bound once: the frame keeps the owning tuple slot
def gen_bound_once(v: Box) -> Iterator[int32]:
    p = make_mixed(1, v)
    yield p[0].n
    p[0].n += 1
    yield p[0].n


# stored into a field at the tuple's last use: the owned element moves, the
# borrowed one copies (warned)
def gen_field_store(v: Box, h: H) -> Iterator[int32]:
    p = make_mixed(1, v)
    p = make_mixed(2, v)
    h.bp = p  # tpyc: warning(/copies Box into field/)
    yield h.bp[0].n


# appended to a list: the list holds copies (warned)
def gen_append(v: Box) -> Iterator[int32]:
    p = make_mixed(1, v)
    p = make_mixed(2, v)
    xs: list[tuple[Box, Box]] = []
    xs.append(p)  # tpyc: warning(/copies Box/)
    yield xs[0][0].n
    # the append leaves `p`'s own element in place
    yield p[0].n


# an unpack target bound before the rebind
def gen_unpack_holder(v: Box, c: bool) -> Iterator[int32]:
    p = make_mixed(1, v)
    x, y = p  # tpyc: ok
    yield x.n
    if c:
        p = make_mixed(9, v)
    x.n += 100
    yield x.n
    yield p[0].n


# a tuple literal holding the element, bound before the rebind
def gen_literal_holder(v: Box, c: bool) -> Iterator[int32]:
    p = make_mixed(1, v)
    r = (p[0], 1)  # tpyc: ok
    yield r[0].n
    if c:
        p = make_mixed(9, v)
    r[0].n += 100
    yield r[0].n
    yield p[0].n


# a chained binding of the element, bound before the rebind
def gen_chained_holder(v: Box, c: bool) -> Iterator[int32]:
    p = make_mixed(1, v)
    a = b = p[0]  # tpyc: ok
    yield a.n
    if c:
        p = make_mixed(9, v)
    a.n += 100
    yield b.n
    yield p[0].n


# a closure capturing an element alias, called after the rebind
def gen_closure_holder(v: Box, c: bool) -> Iterator[int32]:
    p = make_mixed(1, v)
    a = p[0]

    def bump() -> None:
        a.n += 100

    yield a.n
    if c:
        p = make_mixed(9, v)  # tpyc: ok
    bump()
    yield a.n
    yield p[0].n


# a coroutine holding the element across the rebind
async def co_coro_holder(v: Box, c: bool) -> int32:
    p = make_mixed(1, v)
    t = bump_later(p[0])
    if c:
        p = make_mixed(9, v)  # tpyc: ok
    r = await t
    return r * 1000 + p[0].n


# a rebind in a with body
def gen_with_body(v: Box) -> Iterator[int32]:
    p = make_mixed(1, v)
    q = p
    with Ctx():
        p = make_mixed(9, v)  # tpyc: ok
    yield p[0].n
    q[0].n += 100
    yield q[0].n


# a closure reading the alias after the rebind
def gen_closure_read(v: Box) -> Iterator[int32]:
    p = make_mixed(1, v)
    q = p

    def rd() -> int32:
        return q[0].n

    p = make_mixed(9, v)  # tpyc: ok
    q[0].n += 100
    yield rd()
    yield p[0].n


# the whole pointer tuple as a call argument
def gen_call_arg(v: Box) -> Iterator[int32]:
    p = make_mixed(1, v)
    p = make_mixed(2, v)
    q = p
    yield read(p)  # tpyc: ok
    yield q[0].n


async def co_call_arg(v: Box) -> int32:
    p = make_mixed(1, v)
    p = make_mixed(2, v)
    q = p
    await asyncio.sleep(0)
    r = read(p)  # tpyc: ok
    return r * 1000 + q[0].n


# enumerate over the ints of an owned element (plain function twin): the
# loop variables are ints, which keep no borrow
def sync_enumerate_ints(c: bool) -> None:
    t = (N(1), 5)
    total = 0
    for i, x in enumerate(t[0].ns):  # tpyc: ok
        total += x * (i + 1)
    if c:
        t = (N(10), 6)
    print("sync_enumerate_ints", total, t[0].ns[0])


def gen_enumerate_ints(c: bool) -> Iterator[int32]:
    t = (N(1), 5)
    for i, x in enumerate(t[0].ns):  # tpyc: ok
        yield x * (i + 1)
    if c:
        t = (N(10), 6)
    yield t[0].ns[0]


# enumerate over the ints of an owned element with the rebind INSIDE the
# loop: the running iterator holds the old element, so the rebind gives the
# new object storage of its own
def sync_enumerate_rebind_in_loop(c: bool) -> None:
    t = (N(1), 5)
    total = 0
    for i, x in enumerate(t[0].ns):  # tpyc: ok
        if c:
            t = (N(10), 6)
        total += x * (i + 1)
    print("sync_enumerate_rebind_in_loop", total, t[0].ns[0])


def gen_enumerate_rebind_in_loop(c: bool) -> Iterator[int32]:
    t = (N(1), 5)
    for i, x in enumerate(t[0].ns):  # tpyc: ok
        if c:
            t = (N(10), 6)
        yield x * (i + 1)
    yield t[0].ns[0]


# the scalar twin: a record local rebound while a loop iterates its field
def sync_scalar_iter_rebind(c: bool) -> None:
    p = N(1)
    out: list[int32] = []
    for x in p.ns:
        if c:
            p = N(10)  # tpyc: ok
        out.append(x)
    print("sync_scalar_iter_rebind", out, p.ns[0])


def gen_scalar_iter_rebind(c: bool) -> Iterator[int32]:
    p = N(1)
    for x in p.ns:
        if c:
            p = N(10)  # tpyc: ok
        yield x
    yield p.ns[0]


# the two-step rewrite in a plain function
def sync_two_step(c: bool) -> None:
    t = (G(1), 5)
    inner = t[0]
    saved = inner.xs[1]  # tpyc: ok
    if c:
        t = (G(10), 6)
    saved.n += 100
    print("sync_two_step", saved.n, t[0].xs[1].n)


# deleting an element alias leaves the element's object in place
def gen_del_alias(c: bool) -> Iterator[int32]:
    t = (G(1), 5)
    inner = t[0]
    saved = inner.xs[1]
    del inner  # tpyc: ok
    yield 0
    if c:
        t = (G(10), 6)
    saved.n += 100
    yield saved.n
    yield t[0].xs[1].n


# a value type of ints read out of an owned element is a copy
def sync_value_type_holder(c: bool) -> None:
    t = (N(1), 5)
    pt = t[0].pt  # tpyc: ok
    if c:
        t = (N(10), 6)
    print("sync_value_type_holder", pt.x, pt.y, t[0].pt.x)


def gen_value_type_holder(c: bool) -> Iterator[int32]:
    t = (N(1), 5)
    pt = t[0].pt  # tpyc: ok
    yield pt.x
    if c:
        t = (N(10), 6)
    yield pt.y
    yield t[0].pt.x


# a loop over the indices of an owned element's list keeps no borrow of it,
# so the rebind inside the loop writes in place
def sync_range_len(c: bool) -> None:
    t = (N(1), 5)
    out: list[int32] = []
    for i in range(len(t[0].ns)):  # tpyc: ok
        out.append(t[0].ns[i])
        if c:
            t = (N(10), 6)
    print("sync_range_len", out)


def gen_range_len(c: bool) -> Iterator[int32]:
    t = (N(1), 5)
    for i in range(0, len(t[0].ns)):  # tpyc: ok
        yield t[0].ns[i]
        if c:
            t = (N(10), 6)


# a computed bound over an owned element's list keeps no borrow of it either
def sync_range_len_minus(c: bool) -> None:
    t = (N(1), 5)
    out: list[int32] = []
    for i in range(len(t[0].ns) - 1):  # tpyc: ok
        out.append(t[0].ns[i])
        if c:
            t = (N(10), 6)
    print("sync_range_len_minus", out)


# a rebind right before an unconditional `break` leaves the loop: its iterator
# is never pulled again, so nothing reads the object the rebind replaces
def sync_rebind_then_break(c: bool) -> None:
    t = (N(1), 5)
    out: list[int32] = []
    for k in range(3):
        for _i, x in enumerate(t[0].ns):
            out.append(x)
            if c:
                t = (N(10 * (k + 1)), 6)  # tpyc: ok
                break
    print("sync_rebind_then_break", out, t[0].ns[0])


def gen_rebind_then_break(c: bool) -> Iterator[int32]:
    t = (N(1), 5)
    for k in range(3):
        for _i, x in enumerate(t[0].ns):
            yield x
            if c:
                t = (N(10 * (k + 1)), 6)  # tpyc: ok
                break
    yield t[0].ns[0]


# an owned `str` read out of an owned element is a copy of the string
def sync_str_holder(c: bool) -> None:
    t = (Named(1), 5)
    v = t[0].s  # tpyc: ok
    if c:
        t = (Named(2), 6)
    print("sync_str_holder", v, t[0].s)


def gen_str_holder(c: bool) -> Iterator[str]:
    t = (Named(1), 5)
    name = t[0].s  # tpyc: ok
    yield name
    if c:
        t = (Named(2), 6)
    yield name
    yield str(t[1])


# a generic value type instantiated with ints read out of an owned element
# is a copy
def gen_generic_value_holder(c: bool) -> Iterator[int32]:
    t = (Named(1), 5)
    pv = t[0].pv  # tpyc: ok
    yield pv.a
    if c:
        t = (Named(2), 6)
    yield pv.a
    yield t[0].pv.a


# the two-step rewrite across an await: a coroutine built from the holder
# keeps the element object the rebind leaves in place
async def co_two_step(c: bool) -> int32:
    t = (G(1), 5)
    inner = t[0]
    saved = inner.xs[1]  # tpyc: ok
    co = bump_later(saved)
    if c:
        t = (G(10), 6)
    r = await co
    return r * 1000 + t[0].xs[1].n


# a tuple of ints read out of an owned element is a copy
def gen_scalar_tuple_holder(c: bool) -> Iterator[int32]:
    t = (N(1), 5)
    pr = t[0].ps[0]  # tpyc: ok
    yield pr[0]
    if c:
        t = (N(10), 6)
    yield pr[1]
    yield t[0].ns[0]


def sync_scalar_tuple_holder(c: bool) -> None:
    t = (N(1), 5)
    pr = t[0].ps[0]  # tpyc: ok
    if c:
        t = (N(10), 6)
    print("sync_scalar_tuple_holder", pr[0], pr[1], t[0].ns[0])


# a mixed return unpacked in a generator (no `Own[tuple]` frame unpack): the
# owned half is the frame's, the borrowed half aliases the argument
def gen_unpack_mixed(v: Box) -> Iterator[int32]:
    a, b = make_mixed(1, v)  # tpyc: ok
    yield a.n
    # the write through the borrowed half reaches the caller's `v`
    b.n += 100
    yield b.n


class Keeper:
    p: Ptr[Box]

    def __init__(self, b: Box) -> None:
        self.p = take_ptr(b)

    def save(self, b: Box) -> int32:
        self.p = take_ptr(b)
        return 1


class Setter:
    b: Box
    k: Ptr[Keeper]

    def __init__(self, n: int32, k: Keeper) -> None:
        self.b = Box(n)
        self.k = take_ptr(k)

    @property
    def value(self) -> int32:
        return self.b.n

    @value.setter
    def value(self, v: int32) -> None:
        self.k.save(self.b)


def pick_box(b: Box) -> tuple[Box, int32]:
    return (b, 0)


async def box_value(b: Box) -> int32:
    await asyncio.sleep(0)
    return b.n


# A holder bound by a path into an owned element that no tracked loan records
# keeps the old element's object across the rebind and the suspensions.
def gen_untracked_two_hop(c: bool) -> Iterator[int32]:
    t = (G(1), 5)
    # gen_untracked_two_hop: two hops into element 0.
    saved = t[0].xs[1]  # tpyc: ok
    yield saved.n
    if c:
        t = (G(10), 6)
    saved.n += 100
    yield saved.n
    yield t[0].xs[1].n


def gen_untracked_call_unpack(c: bool) -> Iterator[int32]:
    t = (G(1), 5)
    # gen_untracked_call_unpack: a call lends the element's path.
    saved, k = pick_box(t[0].xs[1])  # tpyc: ok
    yield saved.n
    if c:
        t = (G(10), 6)
    saved.n += 100
    yield saved.n + k
    yield t[0].xs[1].n


async def co_untracked_coroutine(c: bool) -> int32:
    t = (G(1), 5)
    # co_untracked_coroutine: a coroutine built from the holder keeps it.
    saved = t[0].xs[1]  # tpyc: ok
    co = box_value(saved)
    if c:
        t = (G(10), 6)
    return await co + 1000 * t[0].xs[1].n


# A list local of tuples rebound under an element alias: the old list keeps
# the alias's object.
def gen_list_of_tuples_alias(c: bool) -> Iterator[int32]:
    xs = [(Box(1), Box(2))]
    # gen_list_of_tuples_alias: the alias reaches into the list's tuple.
    saved = xs[0][1]  # tpyc: ok
    yield saved.n
    if c:
        xs = [(Box(9), Box(8))]
    saved.n += 100
    yield saved.n
    yield xs[0][1].n


# A callee that keeps a pointer to the element: the old element's object
# outlives the rebind.
def gen_retain(c: bool) -> Iterator[int32]:
    k = Keeper(Box(99))
    t = (Box(1), 0)
    # gen_retain: the element's address leaves through the call.
    k.save(t[0])  # tpyc: ok
    yield 0
    if c:
        t = (Box(10), 0)
    k.p.n += 100
    yield k.p.n
    yield t[0].n


async def co_retain(c: bool) -> int32:
    k = Keeper(Box(99))
    t = (Box(1), 0)
    # co_retain: the async twin.
    k.save(t[0])  # tpyc: ok
    await asyncio.sleep(0)
    if c:
        t = (Box(10), 0)
    k.p.n += 100
    return k.p.n * 1000 + t[0].n


# A property setter keeps a pointer into the record local it runs on: the
# record's rebind leaves the old object alive (the scalar face).
def gen_setter_retain(c: bool) -> Iterator[int32]:
    k = Keeper(Box(99))
    s = Setter(1, k)
    # gen_setter_retain: the setter hands out the address of `s.b`.
    s.value = 3  # tpyc: ok
    yield 0
    if c:
        s = Setter(10, k)
    k.p.n += 100
    yield k.p.n
    yield s.b.n


async def co_setter_retain(c: bool) -> int32:
    k = Keeper(Box(99))
    s = Setter(1, k)
    # co_setter_retain: the async twin.
    s.value = 3  # tpyc: ok
    await asyncio.sleep(0)
    if c:
        s = Setter(10, k)
    k.p.n += 100
    return k.p.n * 1000 + s.b.n


def main() -> None:
    v = Box(50)
    w = Box(60)
    h = H(Box(70), Box(80))
    for x in gen_alias_after_rebinds(v):
        print("gen_alias_after_rebinds", x)
    print("co_alias_after_rebinds", asyncio.run(co_alias_after_rebinds(v)))
    for x in gen_rebind_live_whole(v, w):
        print("gen_rebind_live_whole", x)
    for x in gen_rebind_live_elem(v, True):
        print("gen_rebind_live_elem", x)
    print("co_rebind_live_elem", asyncio.run(co_rebind_live_elem(v, True)))
    for x in gen_literal_elem_alias(True):
        print("gen_literal_elem_alias", x)
    print("co_mixed_elem_alias", asyncio.run(co_mixed_elem_alias(v, True)))
    for x in gen_nocopy_elem_alias(True):
        print("gen_nocopy_elem_alias", x)
    for c in [False, True]:
        a = Box(1)
        for x in gen_borrowed_then_owned(a, c):
            print("gen_borrowed_then_owned", c, x)
    for c in [True, False]:
        a = Box(1)
        for x in gen_call_borrow_branches(a, c):
            print("gen_call_borrow_branches", c, x)
    for x in gen_loop_rebind(v):
        print("gen_loop_rebind", x)
    for x in gen_match_arm(v, w, True):
        print("gen_match_arm", x)
    for x in gen_try_finally(v, w):
        print("gen_try_finally", x)
    for x in gen_block_first(v, True):
        print("gen_block_first", x)
    for x in gen_owned_owned():
        print("gen_owned_owned", x)
    for x in gen_repoint_param_field(v, h):
        print("gen_repoint_param_field", x)
    for x in h.walk(v):
        print("gen_method", x)
    for x in gen_call_alias(True):
        print("gen_call_alias", x)
    for x in gen_genexpr_live():
        print("gen_genexpr_live", x)
    for x in gen_readonly_elem(Box(5)):
        print("gen_readonly_elem", x)
    for x in gen_unpack_after(v):
        print("gen_unpack_after", x)
    print("co_await_element", asyncio.run(co_await_element(v)))
    for x in gen_two_hops_borrowed(h, True):
        print("gen_two_hops_borrowed", x)
    for x in gen_two_step(True):
        print("gen_two_step", x)
    for x in gen_all_borrow(Box(50), Box(60)):
        print("gen_all_borrow", x)
    for x in gen_bound_once(Box(1)):
        print("gen_bound_once", x)
    for x in gen_field_store(Box(1), H(Box(3), Box(4))):
        print("gen_field_store", x)
    for x in gen_append(Box(1)):
        print("gen_append", x)
    for x in gen_unpack_holder(v, True):
        print("gen_unpack_holder", x)
    for x in gen_literal_holder(v, True):
        print("gen_literal_holder", x)
    for x in gen_chained_holder(v, True):
        print("gen_chained_holder", x)
    for x in gen_closure_holder(v, True):
        print("gen_closure_holder", x)
    print("co_coro_holder", asyncio.run(co_coro_holder(v, True)))
    for x in gen_with_body(v):
        print("gen_with_body", x)
    for x in gen_closure_read(v):
        print("gen_closure_read", x)
    for x in gen_call_arg(v):
        print("gen_call_arg", x)
    print("co_call_arg", asyncio.run(co_call_arg(v)))
    sync_enumerate_ints(True)
    for x in gen_enumerate_ints(True):
        print("gen_enumerate_ints", x)
    sync_enumerate_rebind_in_loop(True)
    for x in gen_enumerate_rebind_in_loop(True):
        print("gen_enumerate_rebind_in_loop", x)
    sync_scalar_iter_rebind(True)
    for x in gen_scalar_iter_rebind(True):
        print("gen_scalar_iter_rebind", x)
    sync_two_step(True)
    for x in gen_del_alias(True):
        print("gen_del_alias", x)
    sync_value_type_holder(True)
    for x in gen_value_type_holder(True):
        print("gen_value_type_holder", x)
    sync_range_len(True)
    for x in gen_range_len(True):
        print("gen_range_len", x)
    sync_range_len_minus(True)
    sync_rebind_then_break(True)
    for x in gen_rebind_then_break(True):
        print("gen_rebind_then_break", x)
    sync_str_holder(True)
    for s in gen_str_holder(True):
        print("gen_str_holder", s)
    for x in gen_generic_value_holder(True):
        print("gen_generic_value_holder", x)
    print("co_two_step", asyncio.run(co_two_step(True)))
    for x in gen_scalar_tuple_holder(True):
        print("gen_scalar_tuple_holder", x)
    sync_scalar_tuple_holder(True)
    for x in gen_untracked_two_hop(True):
        print("gen_untracked_two_hop", x)
    for x in gen_untracked_call_unpack(True):
        print("gen_untracked_call_unpack", x)
    print("co_untracked_coroutine", asyncio.run(co_untracked_coroutine(True)))
    for x in gen_list_of_tuples_alias(True):
        print("gen_list_of_tuples_alias", x)
    for x in gen_retain(True):
        print("gen_retain", x)
    print("co_retain", asyncio.run(co_retain(True)))
    for x in gen_setter_retain(True):
        print("gen_setter_retain", x)
    print("co_setter_retain", asyncio.run(co_setter_retain(True)))
    vm = Box(5)
    for x in gen_unpack_mixed(vm):
        print("gen_unpack_mixed", x)
    print("gen_unpack_mixed v", vm.n)


main()
