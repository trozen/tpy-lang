# Every read of an Optional element of a tuple behaves as a `Box | None`
# name: bound, tested, passed, rebound, selected and matched, from each
# source kind; each section writes through the read and prints the original.
import asyncio
from typing import Callable, Iterator
from tpy import Own, int32, nocopy, readonly


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __str__(self) -> str:
        return f"box{self.n}"


class H:
    t: tuple[Box | None, int32]
    p: tuple[int32, Box]

    def __init__(self, b: Box) -> None:
        self.t = (Box(b.n), 1)
        self.p = (7, Box(2))

    def branch_bind(self, c: bool) -> None:
        # a branch re-points an outer local at a field-held element
        e: Box | None = None
        if c:
            e = self.t[0]  # tpyc: ok
        if e is not None:
            e.n = 45
        print("branch method", self.t[0] is not None)


def touch(o: Box | None) -> int32:
    if o is not None:
        o.n += 100
        return o.n
    return -1


def peek(o: Box | None) -> int32:
    return -1 if o is None else o.n


def lend(h: H) -> tuple[Box | None, int32]:
    return h.t


def mk_own(n: int32) -> tuple[Own[Box] | None, int32]:
    return (Box(n), n)


def mk_none() -> tuple[Box | None, int32]:
    return (None, 0)


# --- bind ---------------------------------------------------------------------

def bind_literal(b: Box) -> None:
    # bind: literal-built local
    t: tuple[Box | None, int32] = (b, 1)
    e = t[0]  # tpyc: ok
    if e is not None:
        e.n = 11
    print("bind literal", b.n)


def bind_param(t: tuple[Box | None, int32], b: Box) -> None:
    # bind: borrowed parameter
    e = t[0]  # tpyc: ok
    if e is not None:
        e.n = 12
    print("bind param", b.n)


def bind_lent(h: H) -> None:
    # bind: a lending call's element
    r = lend(h)
    e = r[0]  # tpyc: ok
    if e is not None:
        e.n = 13
    print("bind lent", peek(h.t[0]))


def bind_none() -> None:
    # bind: a None-built element
    t = mk_none()
    e = t[0]  # tpyc: ok
    print("bind none", e is None)


def bind_owned(n: int32) -> None:
    # bind: an element the tuple stores (`std::optional<Box>`), lifted
    t = mk_own(n)
    e = t[0]  # tpyc: ok
    if e is not None:
        e.n = 19
    u = t[0]
    print("bind owned", u is not None and u.n == 19)


def bind_readonly(t: readonly[tuple[Box | None, int32]]) -> None:
    # bind: an element of a readonly tuple binds a const pointer
    e = t[0]  # tpyc: ok
    print("bind readonly", e is not None and e.n > 0)


# --- is None -------------------------------------------------------------------

def test_param(t: tuple[Box | None, int32]) -> None:
    # is None: parameter element
    print("isnone param", t[0] is None, t[0] is not None)  # tpyc: ok


def test_literal() -> None:
    # is None: literal-built local
    c = Box(9)
    t: tuple[Box | None, int32] = (c, 1)
    print("isnone literal", t[0] is not None)  # tpyc: ok


def test_field(h: H) -> None:
    # is None: field element (storage-held)
    print("isnone field", h.t[0] is None)  # tpyc: ok


def test_then_read(t: tuple[Box | None, int32]) -> None:
    # is None, then a member read: the element is not narrowed
    # (BUGS.md#tuple-optional-element-not-narrowed), so the read is checked
    if t[0] is not None:  # tpyc: ok
        print("isnone then read", t[0].n)  # tpyc: warning(/Potential None access/)


# --- argument ------------------------------------------------------------------

def arg_param(t: tuple[Box | None, int32], b: Box) -> None:
    # argument: parameter element
    touch(t[0])  # tpyc: ok
    print("arg param", b.n)


def arg_field(h: H) -> None:
    # argument: field element (storage-held, lifted)
    touch(h.t[0])  # tpyc: ok
    print("arg field", touch(h.t[0]))


# --- rebind --------------------------------------------------------------------

def rebind(t: tuple[Box | None, int32], o: Box | None) -> None:
    # rebind: the element local re-pointed at another Optional
    e = t[0]  # tpyc: ok
    e = o
    if e is not None:
        e.n = 15
    print("rebind", peek(o))


def rebind_elem(t: tuple[Box | None, Box | None]) -> None:
    # rebind: re-pointed at the tuple's other Optional element
    e = t[0]  # tpyc: ok
    e = t[1]  # tpyc: ok
    if e is not None:
        e.n = 22
    print("rebind elem", touch(t[1]))


# --- ternary -------------------------------------------------------------------

def ternary(t: tuple[Box | None, int32], c: bool, b: Box) -> None:
    # ternary: an element arm beside None
    x = t[0] if c else None  # tpyc: ok
    if x is not None:
        x.n = 16
    print("ternary", b.n)


# --- match ---------------------------------------------------------------------

def matched(t: tuple[Box | None, int32], b: Box) -> None:
    # match: an element subject
    match t[0]:  # tpyc: ok
        case Box() as m:
            m.n = 17
        case _:
            pass
    print("match", b.n)


def matched_lent(h: H) -> None:
    # match: an element a lending call's tuple holds as a pointer
    r = lend(h)
    match r[0]:  # tpyc: ok
        case Box() as m:
            m.n = 23
        case _:
            pass
    print("match lent", peek(h.t[0]))


# --- stored elements at the other sinks ------------------------------------------

def owned_sinks(n: int32) -> None:
    # argument and test of an element the tuple stores
    t = mk_own(n)
    touch(t[0])  # tpyc: ok
    print("owned sinks", peek(t[0]), t[0] is not None)  # tpyc: ok


# --- return --------------------------------------------------------------------

def first(t: tuple[Box | None, int32]) -> Box | None:
    # return: the element the tuple holds as a pointer
    return t[0]  # tpyc: ok


def returned(b: Box) -> None:
    r = first((b, 1))
    if r is not None:
        r.n = 24
    print("return", b.n)


@nocopy
class N:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class HN:
    t: tuple[N | None, int32]

    def __init__(self, n: int32) -> None:
        self.t = (N(n), n)


def stored_first(h: HN) -> N | None:
    # return: an element the tuple stores lifts to its pointer, never a copy
    return h.t[0]  # tpyc: ok


def returned_stored() -> None:
    h = HN(3)
    r = stored_first(h)
    if r is not None:
        r.n = 25
    s = stored_first(h)
    print("return stored", s is not None and s.n == 25)


# --- record elements stay as they were ------------------------------------------

def rec_arg(t: tuple[Box, int32]) -> None:
    # record element at an Optional parameter: the pointer the tuple holds
    print("rec arg", touch(t[0]))  # tpyc: ok


def rec_print(b: Box) -> None:
    # an inline record element of a mixed tuple prints the object
    t = (Box(1), b)
    print("rec print", t[0])  # tpyc: ok
    print("rec print", t[1].n)


def mk_mixed(b: Box) -> tuple[Own[Box], Box]:
    return (Box(5), b)


def mk_pair(v: int32) -> tuple[int32, Own[Box]]:
    return (v, Box(v))


def rec_risk(b: Box, h: H) -> None:
    # record elements off a mixed call, a walrus owned slot and a rebound
    # walrus: `.` where the tuple holds the element inline, `->` where a
    # pointer
    print("rec mixed call", mk_mixed(b)[0].n, mk_mixed(b)[1].n)  # tpyc: ok
    if (w := mk_mixed(b))[0].n > 0:  # tpyc: ok
        w[1].n += 1
    print("rec walrus", w[0].n, b.n)
    first_v = (t := mk_pair(9))[1].n  # tpyc: ok
    t = h.p
    print("rec walrus rebound", first_v, t[1].n)


def rec_frame(b: Box) -> Iterator[int32]:
    # frame-own: the inline element `.`, the pointer element `->`
    t = (Box(3), b)
    t[1].n = 18
    yield t[0].n
    yield t[1].n


class W:
    def __init__(self) -> None:
        pass

    def meth(self, t: tuple[Box | None, int32], b: Box) -> None:
        # method body: bind and test
        e = t[0]  # tpyc: ok
        if e is not None and t[0] is not None:
            e.n = 20
        print("method", b.n)


def gen(t: tuple[Box | None, int32], b: Box) -> Iterator[int32]:
    # generator body: bind across a suspension
    e = t[0]  # tpyc: ok
    yield 1
    if e is not None:
        e.n = 21
    yield b.n


# --- more sources and sinks --------------------------------------------------

def bind_direct_lent(h: H) -> None:
    # bind: an element of a lending call read directly
    e = lend(h)[0]  # tpyc: ok
    if e is not None:
        e.n = 30
    print("bind direct lent", peek(h.t[0]))


def bind_field(h: H) -> None:
    # bind: a field-held element lifts out of the stored optional
    e = h.t[0]  # tpyc: ok
    if e is not None:
        e.n = 31
    print("bind field", peek(h.t[0]))


def bind_alias(h: H) -> None:
    # bind: an element of a storage-tuple alias of a field
    a = h.t
    e = a[0]  # tpyc: ok
    if e is not None:
        e.n = 32
    print("bind alias", peek(h.t[0]))


def bind_own_readonly() -> None:
    # bind: an `Own[readonly[Box]] | None` element binds a const pointer
    t = mk_ro(4)
    e = t[0]  # tpyc: ok
    print("bind own readonly", e is not None and e.n == 4)


def mk_ro(n: int32) -> tuple[Own[readonly[Box]] | None, int32]:
    return (Box(n), n)


def own_param(t: tuple[Own[Box] | None, int32]) -> Own[Box] | None:
    # an `Own[P] | None` parameter element: bound, then returned (moved out)
    e = t[0]  # tpyc: ok
    if e is not None:
        e.n = 33
    return t[0]  # tpyc: ok


def own_param_nested(t: tuple[Own[Box] | None, int32]) -> int32:
    # an `Own[P] | None` parameter element read in a nested def
    def inner() -> int32:
        a = t[0]  # tpyc: ok
        if a is not None:
            a.n = 44
        return peek(a)
    return inner()


def own_param_call() -> None:
    r = own_param((Box(2), 1))
    if r is not None:
        print("own param", r.n, own_param_nested((Box(3), 1)))


def arg_more(h: H) -> None:
    # argument: literal-built, lent and None-built elements
    c = Box(1)
    t: tuple[Box | None, int32] = (c, 1)
    touch(t[0])  # tpyc: ok
    r = lend(h)
    touch(r[0])  # tpyc: ok
    z = mk_none()
    print("arg more", c.n, peek(h.t[0]), touch(z[0]))  # tpyc: ok


def test_more(h: H) -> None:
    # is None: lent and None-built elements
    r = lend(h)
    z = mk_none()
    print("isnone more", r[0] is None, z[0] is None)  # tpyc: ok


def match_more(h: H) -> None:
    # match: literal-built, None-built and field-held subjects
    c = Box(1)
    t: tuple[Box | None, int32] = (c, 1)
    match t[0]:  # tpyc: ok
        case Box() as m:
            m.n = 35
        case _:
            pass
    z = mk_none()
    match z[0]:  # tpyc: ok
        case Box() as m:
            m.n = 0
        case _:
            print("match none")
    match h.t[0]:  # tpyc: ok
        case Box() as m:
            m.n = 36
        case _:
            pass
    print("match more", c.n, peek(h.t[0]))


def ternary_field(h: H, c: bool) -> None:
    # ternary: a field-held element arm
    x = h.t[0] if c else None  # tpyc: ok
    if x is not None:
        x.n = 37
    print("ternary field", peek(h.t[0]))


def rebind_rvalue(n: int32) -> None:
    # rebind: a stored element's local later bound to a fresh object
    t = mk_own(n)
    e = t[0]  # tpyc: ok
    e = Box(9)
    e.n = 38
    print("rebind rvalue", peek(t[0]), e.n)


def rebind_branch(t: tuple[Box | None, int32], c: bool) -> None:
    # rebind: a branch rebinds the local, then it is re-pointed at the element
    e = t[0]  # tpyc: ok
    if c:
        e = Box(9)
    print("rebind branch", peek(e))
    e = t[0]  # tpyc: ok
    if e is not None:
        e.n = 39


# --- positions -----------------------------------------------------------------

def pos_closure(t: tuple[Box | None, int32]) -> None:
    # closure: a nested def binds and tests the captured tuple's element
    def inner() -> None:
        e = t[0]  # tpyc: ok
        if e is not None and t[0] is not None:
            e.n = 40
    inner()
    print("closure", peek(t[0]))


def pos_loop(ts: list[tuple[Box | None, int32]]) -> None:
    # loop body: each step's element
    for t in ts:
        e = t[0]  # tpyc: ok
        if e is not None:
            e.n += 1
    for t in ts:
        print("loop", peek(t[0]))


async def pos_async(t: tuple[Box | None, int32]) -> int32:
    # async: bind across an await
    e = t[0]  # tpyc: ok
    await asyncio.sleep(0)
    if e is not None:
        e.n = 41
    return peek(t[0])


def pos_try(t: tuple[Box | None, int32]) -> None:
    # try/finally: bind in the body, test in the finally
    try:
        e = t[0]  # tpyc: ok
        if e is not None:
            e.n = 42
    finally:
        print("try", t[0] is not None)  # tpyc: ok
    print("try after", peek(t[0]))


def pos_lambda(t: tuple[Box | None, int32]) -> None:
    # lambda: an element passed on inside a lambda body; the hedged copy
    # warning cannot diverge here (BUGS.md#hedged-capture-copy-unrebound-param)
    f: Callable[[], int32] = lambda: touch(t[0])  # tpyc: warning(/may keep its own copy of 't'/)
    print("lambda", f())


def gen_param(p: tuple[Box | None, int32]) -> Iterator[int32]:
    # generator: a test of a parameter's element
    if p[0] is not None:  # tpyc: ok
        yield 1
    yield 2


def gen_steps(b: Box) -> Iterator[tuple[int32, Box | None]]:
    yield (1, b)


def match_steps(b: Box) -> None:
    # match over a generator step's lent element
    for q in gen_steps(b):
        match q[1]:  # tpyc: ok
            case Box() as m:
                m.n = 43
            case _:
                pass
    print("match steps", b.n)


def branch_alias(h: H, c: bool) -> None:
    # a branch re-points an outer local at an element of an alias taken
    # before it
    e: Box | None = None
    a = h.t
    if c:
        e = a[0]  # tpyc: ok
    if e is not None:
        e.n = 46
    print("branch alias", peek(h.t[0]))


def branch_param(t: tuple[Box | None, int32], c: bool) -> None:
    # a parameter's element first bound in a branch, None in the other
    if c:
        e = t[0]  # tpyc: ok
    else:
        e = None
    if e is not None:
        e.n = 47
    print("branch param", peek(t[0]))


def branch_pointer_tuple(b: Box, c: bool) -> None:
    # a pointer tuple built in a block over a parameter: the element
    # outlives the block
    e: Box | None = None
    if c:
        t: tuple[Box | None, int32] = (b, 1)
        e = t[0]  # tpyc: ok
    if e is not None:
        e.n = 48
    print("branch pointer tuple", b.n)


def branch_block_tuple(c: bool) -> None:
    # a storage-form tuple bound in a block, its element bound, written and
    # read only inside that block
    if c:
        t = mk_own(4)
        e = t[0]  # tpyc: ok
        if e is not None:
            e.n = 49
        print("branch block tuple", peek(t[0]))


def own_ternary(c: bool) -> None:
    # a ternary over an `Own[Box] | None` element binds `Box*`, as the
    # bare `Box | None` element's does: a write through it reaches the tuple
    # (an owning arm keeps `Own` and refuses:
    # BUGS.md#own-optional-target-alias-rejects)
    t = mk_own(2)
    x = t[0] if c else None  # tpyc: ok
    if x is not None:
        x.n = 51
    print("own ternary", c, peek(t[0]), peek(x))


def main() -> None:
    b = Box(1)
    bind_literal(b)
    bind_param((b, 1), b)
    h = H(b)
    bind_lent(h)
    bind_none()
    bind_owned(3)
    bind_readonly((b, 1))
    test_param((b, 1))
    test_param((None, 1))
    test_literal()
    test_field(h)
    test_then_read((b, 1))
    W().meth((b, 1), b)
    for v in gen((b, 1), b):
        print("gen", v)
    arg_param((b, 1), b)
    arg_field(h)
    rebind((b, 1), Box(2))
    rebind_elem((None, b))
    ternary((b, 1), True, b)
    matched((b, 1), b)
    matched_lent(h)
    owned_sinks(7)
    returned(b)
    returned_stored()
    rec_arg((b, 1))
    rec_print(b)
    rec_risk(b, h)
    for v in rec_frame(b):
        print("rec frame", v)
    bind_direct_lent(h)
    bind_field(h)
    bind_alias(h)
    bind_own_readonly()
    own_param_call()
    arg_more(h)
    test_more(h)
    match_more(h)
    ternary_field(h, True)
    rebind_rvalue(3)
    rebind_branch((b, 1), True)
    print("rebind branch after", b.n)
    pos_closure((b, 1))
    pos_loop([(Box(5), 1)])
    print("async", asyncio.run(pos_async((b, 1))))
    pos_try((b, 1))
    pos_lambda((b, 1))
    for v in gen_param((b, 1)):
        print("gen param", v)
    match_steps(b)
    h.branch_bind(True)
    print("branch method after", peek(h.t[0]))
    branch_alias(h, True)
    branch_param((b, 1), True)
    branch_pointer_tuple(b, True)
    branch_block_tuple(True)
    own_ternary(True)
    own_ternary(False)


main()
