# A write through a local alias of an owner's field -- passing it to a
# writer, calling a mutating method on it, storing through it -- writes the
# owner, so the owner stays mutable in every position; an alias that only
# reads, a member that points elsewhere (a Ptr, a getter returning a fresh
# object or a handle) or a rebound alias leaves the owner const.
import asyncio
from typing import Iterator

from tpy import (int32, Own, Ptr, Span, error_return, readonly,
                 ReturnException, take_ptr)
from tplib import Box, Rc

g_xs: list[int32] = [1, 2]


class Cell:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def inc(self) -> None:
        self.n += 1

    def ro(self) -> int32:
        return self.n


class Mid:
    cell: Cell

    def __init__(self) -> None:
        self.cell = Cell(1)

    # readonly (inferred: it writes nothing of `self`), lending `c`.
    def pick(self, c: Cell) -> Cell:
        return c

    def ticks(self) -> Iterator[int32]:
        self.cell.n += 1
        yield self.cell.n


class P:
    cell: Cell
    a: Mid
    kid: list[int32]
    opt: list[int32] | None
    pair: tuple[Cell, int32]
    cells: list[Cell]
    box: Box[Cell]
    link: Mid | None
    rc: Rc[Cell]

    def __init__(self) -> None:
        self.cell = Cell(1)
        self.a = Mid()
        self.kid = [1]
        self.opt = [1]
        self.pair = (Cell(1), 2)
        self.cells = [Cell(1)]
        self.box = Box(Cell(1))
        self.link = Mid()
        self.rc = Rc.new(Cell(1))

    # method: the receiver is written through its field's alias.
    def self_writer(self) -> None:
        q = self.cell
        bump(q)  # the subject: `self_writer` stays non-const

    # method: two hops, two lines.
    def self_two_hop(self) -> None:
        m = self.a
        q = m.cell
        bump(q)  # the subject: `self_two_hop` stays non-const

    # method: a Box field's alias, written through `get()`.
    def self_box(self) -> None:
        b = self.box
        b.get().inc()  # the subject: `self_box` stays non-const

    # method: a list field's alias grown.
    def self_list(self) -> None:
        row = self.kid
        row.append(99)  # the subject: `self_list` stays non-const

    # method: an alias of a narrowed Optional field's field.
    def self_narrowed(self) -> None:
        nx = self.link
        if nx is not None:
            q = nx.cell
            bump(q)  # the subject: `self_narrowed` stays non-const

    # method, inverse: an element of the alias only read.
    def self_index_ro(self) -> int32:
        xs = self.cells
        return xs[0].ro()  # the subject: `self_index_ro` stays const

    # method, inverse: an element of the alias written.
    def self_index_mut(self) -> None:
        xs = self.cells
        xs[0].inc()  # the subject: `self_index_mut` stays non-const


class Ctor:
    cell: Cell

    # constructor: a field's alias handed to a writer.
    def __init__(self) -> None:
        self.cell = Cell(1)
        q = self.cell
        bump(q)  # the subject: the write lands in the new object


class Q:
    cell: Cell

    def __init__(self) -> None:
        self.cell = Cell(5)


class G:
    cell: Cell

    def __init__(self) -> None:
        self.cell = Cell(1)

    # getter order: a handle getter declared below the method.
    def view_above(self) -> None:
        q = self.view
        q[0] += 1  # the subject: const, like `view_below`

    @property
    def c(self) -> Cell:
        return self.cell

    @property
    def snap(self) -> Own[Cell]:
        return Cell(self.cell.n)

    @property
    def view(self) -> Span[int32]:
        return g_xs

    # getter order: the same getter declared above the method.
    def view_below(self) -> None:
        q = self.view
        q[0] += 1  # the subject: const, like `view_above`


class H:
    p: Ptr[Cell]

    def __init__(self, c: Cell) -> None:
        self.p = take_ptr(c)

    @property
    def pp(self) -> Ptr[Cell]:
        return self.p

    # method, inverse: a Ptr member's alias written.
    def self_ptr(self) -> None:
        q = self.p
        q.n += 1  # the subject: `self_ptr` stays const


class HP:
    po: Ptr[P]

    def __init__(self, o: P) -> None:
        self.po = take_ptr(o)


class W:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def add(self, k: int32) -> None:
        self.n += k


class Inner:
    cell: Cell
    cs: list[Cell]

    def __init__(self) -> None:
        self.cell = Cell(1)
        self.cs = [Cell(2)]

    @property
    def c(self) -> readonly[Cell]:
        return self.cell

    @property
    def cl(self) -> readonly[list[Cell]]:
        return self.cs


class Outer:
    inner: Inner

    def __init__(self) -> None:
        self.inner = Inner()


class E(Exception, ReturnException):
    pass


def bump(c: Cell) -> None:
    c.n += 1


def take(c: Cell) -> int32:
    c.n += 1
    return c.n


def accept[T](x: T) -> None:
    pass


# free function: a field's alias handed to a writer.
def fn_writer(p: P) -> None:
    q = p.cell
    bump(q)  # the subject: `p` stays mutable


# free function: a mutating method on the alias.
def fn_method(p: P) -> None:
    q = p.cell
    q.inc()  # the subject


# free function: a list field's alias grown.
def fn_list(p: P) -> None:
    row = p.kid
    row.append(99)  # the subject


# free function: an Optional field's alias, narrowed, grown.
def fn_optional(p: P) -> None:
    o0 = p.opt
    if o0 is not None:
        x = o0
        x.append(3)  # the subject


# free function: an alias of a narrowed Optional root's field.
def fn_narrowed_optional(o: P | None) -> None:
    if o is not None:
        q = o.cell
        bump(q)  # the subject: `o` stays a mutable pointer


# free function: an alias of an isinstance-narrowed union root's field.
def fn_narrowed_union(o: P | Q) -> None:
    if isinstance(o, P):
        q = o.cell
        bump(q)  # the subject


# free function: two hops, two lines.
def fn_two_hop(p: P) -> None:
    m = p.a
    q = m.cell
    bump(q)  # the subject


# free function: an alias passed to an open-`T` parameter, a mutable
# reference at every reference instantiation.
def fn_open_generic(p: P) -> None:
    q = p.cell
    accept(q)  # the subject


# open-`T` argument inside a loop over a member iterable.
def fn_open_generic_loop(p: P) -> None:
    for x in p.cells:
        accept(x)  # the subject: `p` and `x` stay mutable


# loop over a parameter: each element's field alias written.
def fn_loop(ps: list[P]) -> None:
    for p in ps:
        q = p.cell
        bump(q)  # the subject: `ps` stays mutable


# loop over a member iterable, each element written.
def fn_member_loop(p: P) -> None:
    for c in p.cells:
        bump(c)  # the subject


# loop over a member iterable of a narrowed Optional root.
def fn_member_loop_narrowed(o: P | None) -> None:
    if o is not None:
        for c in o.cells:
            bump(c)  # the subject: `o` stays a mutable pointer


# inverse: a loop over a member iterable past a Ptr member -- the write
# lands in the pointee, `h` stays const. (`for c in h.po.cells` is not
# lowered yet, so the Ptr is aliased first.)
def inv_ptr_member_loop(h: HP) -> None:
    o = h.po
    for c in o.cells:
        bump(c)  # the subject


# comprehension over a member iterable, each element written.
def fn_comprehension(p: P) -> int32:
    return sum([take(c) for c in p.cells])  # the subject


# walrus spelling of a list field's alias, grown.
def fn_walrus(p: P) -> None:
    (row := p.kid).append(99)  # the subject


# an Rc field's alias, written through `get()`.
def fn_rc(p: P) -> None:
    r = p.rc
    r.get().inc()  # the subject


# select of two aliases from two parameters.
def fn_select(p: P, r: P, c: bool) -> None:
    qa = p.cell
    qb = r.cell
    x = qa if c else qb
    x.inc()  # the subject: both `p` and `r` stay mutable


# select arm as the receiver.
def fn_select_arm(p: P, c: bool) -> None:
    x = p.cell
    (x if c else Cell(0)).inc()  # the subject


# match capture of a deep subject.
def fn_match_deep(p: P) -> None:
    match p.a.cell:
        case Cell() as q:
            q.inc()  # the subject


# tuple field alias: its element handed to a writer.
def fn_tuple(p: P) -> None:
    q = p.pair
    bump(q[0])  # the subject


# Box field alias: written through `get()` three ways.
def fn_box(p: P) -> None:
    b = p.box
    bump(b.get())  # the subject
    b.get().n += 1  # the subject
    b.get().inc()  # the subject


# nested def: the alias is written inside the closure.
def fn_closure(p: P) -> None:
    q = p.cell

    def go() -> None:
        q.inc()  # the subject: `p` stays mutable

    go()


# nested def: a captured alias of a loop binding, written.
def fn_closure_loop_alias(ps: list[Cell]) -> None:
    for p in ps:
        q = p

        def go() -> None:
            q.inc()  # the subject: `ps` stays mutable

        go()


# nested def: a `nonlocal` loop variable written by a mutating method.
def fn_closure_nonlocal(vs: list[W]) -> None:
    for w in vs:
        def go() -> None:
            nonlocal w
            w.add(10)  # the subject: `vs` stays mutable

        go()


# nested def: a `zip` head over captured parameters, each element written.
def fn_closure_zip(xs: list[Cell], ys: list[Cell]) -> None:
    def go() -> None:
        for a, b in zip(xs, ys):
            bump(a)  # the subject: `xs` and `ys` stay mutable
            bump(b)

    go()


# nested def: a loop over a captured field alias, each element written.
def fn_closure_field_loop(p: P) -> None:
    ys = p.cells

    def go() -> None:
        for c in ys:
            bump(c)  # the subject: `p` stays mutable

    go()


# generator body.
def gen(p: P) -> Iterator[int32]:
    q = p.cell
    bump(q)  # the subject
    yield q.n


# async body.
async def work(p: P) -> int32:
    q = p.cell
    bump(q)  # the subject
    return q.n


# with body.
class Guard:
    def __init__(self) -> None:
        pass

    def __enter__(self) -> "Guard":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


def fn_with(p: P) -> None:
    with Guard():
        q = p.cell
        bump(q)  # the subject


# try / finally body.
def fn_try(p: P) -> None:
    try:
        q = p.cell
        bump(q)  # the subject
    finally:
        pass


# match arm body.
def fn_match_arm(p: P, k: int32) -> None:
    match k:
        case 1:
            q = p.cell
            bump(q)  # the subject
        case _:
            pass


# @error_return body.
@error_return(E)
def fn_error_return(p: P, k: int32) -> None:
    if k < 0:
        raise E
    q = p.cell
    bump(q)  # the subject


# borrowing getter: it lends the receiver writable, so the owner stays mutable.
def fn_borrowing_getter(s: G) -> None:
    q = s.c
    bump(q)  # the subject


# a writer reached through a field's alias, called while that field is
# iterated: the warning names the field.
class K:
    kid: list[int32]

    def __init__(self) -> None:
        self.kid = [1, 2]


def grow(xs: list[int32]) -> None:
    xs.append(9)


def ext(k: K) -> None:
    r = k.kid
    grow(r)


def fn_iter_warn(k: K) -> None:
    for x in k.kid:
        if x == 1:
            ext(k)  # tpyc: warning(/may move the elements of 'k.kid'/)


# inverse: a getter returning a fresh object -- the owner stays const.
def inv_fresh_getter(s: G) -> int32:
    q = s.snap
    bump(q)
    q.n += 1  # the subject: a write into the new object, not `s`
    return q.n


# inverse: a Ptr member -- the write lands in the pointee, `h` stays const.
def inv_ptr_member(h: H) -> None:
    q = h.p
    q.n += 1  # the subject


# inverse: a Ptr member of a narrowed Optional root, written.
def inv_ptr_narrowed(h: H | None) -> None:
    if h is not None:
        q = h.p
        q.n += 1  # the subject: `h` stays a const pointer


# inverse: a Ptr member of an isinstance-narrowed union root, written.
def inv_ptr_union(h: H | Q) -> None:
    if isinstance(h, H):
        q = h.p
        q.n += 1  # the subject: `h` stays const


# a readonly getter's result returned through an alias: it still borrows
# the parameter, so the warnings below see the loan.
def ro_alias(p: Outer) -> readonly[Cell]:
    q = p.inner.c
    return q


# the same through a loop over a readonly getter's list.
def ro_loop(p: Outer) -> readonly[Cell]:
    for c in p.inner.cl:
        return c
    return p.inner.cell


# the same over two lines.
def ro_two_line(p: Outer) -> readonly[Cell]:
    m = p.inner
    q = m.c
    return q


# inverse: a Ptr member stops the borrow climb -- the result points into
# the pointee, not into the holder.
def via_ptr(h: HP) -> readonly[Cell]:
    o = h.po
    q = o.cell
    return q


def ro_warnings(p: P) -> None:
    _a = ro_alias(Outer())  # tpyc: warning(/Result borrows from temporary argument 'p'/)
    _b = ro_loop(Outer())  # tpyc: warning(/Result borrows from temporary argument 'p'/)
    _c = ro_two_line(Outer())  # tpyc: warning(/Result borrows from temporary argument 'p'/)
    _d = via_ptr(HP(p))  # tpyc: ok
    o = Outer()
    r = ro_alias(o)
    # Read before the replacement: past it CPython's `r` is the old cell,
    # TPy's the replaced storage. The field-assignment warning below flags
    # the replacement while `r` borrows `o`.
    seen = r.n
    o.inner = Inner()  # tpyc: warning(/Mutation of 'o' while borrowed/)
    print("ro_warnings", seen, _d.n)


# a readonly method's result lends its argument, not its receiver: a write
# through it leaves the owner `p` const and writes `c`.
def fn_readonly_method(p: P, c: Cell) -> None:
    x = p.a.pick(c)
    x.inc()  # the subject


# a generator method read off a member path: advancing it runs the body,
# which writes the member, so the owner is mutable. (`p.a.ticks()` in one
# expression is not lowered yet: BUGS.md#generator-method-member-receiver-rejects.)
def fn_gen_method(p: P) -> int32:
    m = p.a
    g = m.ticks()
    for v in g:  # the subject
        return v
    return -1


def consume(g: Iterator[int32]) -> int32:
    for v in g:
        return v
    return -1


# a member alias's generator handed to a callee that advances it: the body
# writes the member, so the owner stays mutable (`P& p` in the signature).
# (`consume(m.ticks())` fails the C++ build:
# BUGS.md#generator-method-temp-at-protocol-param.)
def fn_gen_handed(p: P) -> int32:
    m = p.a
    g = m.ticks()
    return consume(g)  # the subject


# inverse: a Ptr-returning getter.
def inv_ptr_getter(h: H) -> None:
    q = h.pp
    bump(q)  # the subject


# inverse: a read-only alias.
def inv_read_only(p: P) -> int32:
    q = p.cell
    return q.n  # the subject: `p` stays const


# inverse: a read-only alias rebound to another field; nothing writes.
def inv_rebound(p1: P, p2: P) -> int32:
    q1 = p1.cell
    q2 = p2.cell
    r = q1
    r = q2  # the subject: re-pointing `r` writes neither owner
    return r.n


# inverse: the alias of a rebound getter alias -- the chain holds only
# when every hop does, so a write through `r` stays off `s`.
def inv_rebound_getter(s: G) -> int32:
    q = s.snap
    r = q
    q = Cell(10)
    r.n += 1  # the subject: `s` stays const
    return r.n + q.n


def main() -> None:
    p = P()
    fn_writer(p)
    print("fn_writer", p.cell.n)
    fn_method(p)
    print("fn_method", p.cell.n)
    fn_list(p)
    print("fn_list", len(p.kid))
    fn_optional(p)
    o = p.opt
    print("fn_optional", len(o) if o is not None else -1)
    fn_narrowed_optional(p)
    print("fn_narrowed_optional", p.cell.n)
    fn_narrowed_union(p)
    print("fn_narrowed_union", p.cell.n)
    fn_two_hop(p)
    print("fn_two_hop", p.a.cell.n)
    fn_open_generic(p)
    fn_open_generic_loop(p)
    print("fn_open_generic", p.cell.n, p.cells[0].n)
    ps = [P(), P()]
    fn_loop(ps)
    print("fn_loop", ps[0].cell.n, ps[1].cell.n)
    fn_member_loop(p)
    print("fn_member_loop", p.cells[0].n)
    r = P()
    fn_select(p, r, False)
    print("fn_select", p.cell.n, r.cell.n)
    fn_select_arm(p, True)
    print("fn_select_arm", p.cell.n)
    fn_match_deep(p)
    print("fn_match_deep", p.a.cell.n)
    fn_tuple(p)
    print("fn_tuple", p.pair[0].n)
    fn_box(p)
    print("fn_box", p.box.get().n)
    fn_closure(p)
    print("fn_closure", p.cell.n)
    for v in gen(p):
        print("gen", v, p.cell.n)
    print("async", asyncio.run(work(p)), p.cell.n)
    fn_with(p)
    print("fn_with", p.cell.n)
    fn_try(p)
    print("fn_try", p.cell.n)
    fn_match_arm(p, 1)
    print("fn_match_arm", p.cell.n)
    try:
        fn_error_return(p, 1)
    except E:
        print("fn_error_return raised")
    print("fn_error_return", p.cell.n)
    p.self_writer()
    p.self_two_hop()
    p.self_box()
    p.self_index_mut()
    print("method", p.cell.n, p.a.cell.n, p.box.get().n, p.self_index_ro())
    print("ctor", Ctor().cell.n)
    k = K()
    fn_iter_warn(k)
    print("fn_iter_warn", len(k.kid))
    s = G()
    fn_borrowing_getter(s)
    print("fn_borrowing_getter", s.cell.n)
    print("inv_fresh_getter", inv_fresh_getter(s), s.cell.n)
    c = Cell(1)
    h = H(c)
    inv_ptr_member(h)
    inv_ptr_getter(h)
    print("inv_ptr", c.n)
    print("inv_read_only", inv_read_only(p))
    print("inv_rebound", inv_rebound(p, r))
    print("inv_rebound_getter", inv_rebound_getter(s), s.cell.n)
    s.view_above()
    s.view_below()
    print("getter_order", g_xs[0])
    p.self_list()
    p.self_narrowed()
    lk = p.link
    print("self_list", len(p.kid), lk.cell.n if lk is not None else -1)
    h.self_ptr()
    print("self_ptr", c.n)
    fn_member_loop_narrowed(p)
    inv_ptr_member_loop(HP(p))
    print("member_loop", p.cells[0].n, fn_comprehension(p))
    fn_walrus(p)
    fn_rc(p)
    print("fn_walrus", len(p.kid), "fn_rc", p.rc.get().n)
    cs = [Cell(1)]
    fn_closure_loop_alias(cs)
    vs = [W(1)]
    fn_closure_nonlocal(vs)
    fn_closure_zip(cs, p.cells)
    fn_closure_field_loop(p)
    print("closures", cs[0].n, vs[0].n, p.cells[0].n)
    inv_ptr_narrowed(h)
    inv_ptr_union(h)
    print("inv_ptr_narrowed", c.n)
    ro_warnings(p)
    c2 = Cell(1)
    fn_readonly_method(p, c2)
    print("fn_readonly_method", c2.n, p.a.cell.n)
    print("fn_gen_method", fn_gen_method(p), p.a.cell.n)
    print("fn_gen_handed", fn_gen_handed(p), p.a.cell.n)


main()
