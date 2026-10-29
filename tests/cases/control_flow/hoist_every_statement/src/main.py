# A local first bound inside a compound statement and read after it is
# predeclared by one driver whatever the statement: every section binds it
# at a different statement and then reads or mutates through it.
from typing import Iterator

from tpy import int32, Own, nocopy, error_return, ReturnException, readonly


class Bad(Exception, ReturnException):
    pass


class Box:
    val: int32

    def __init__(self, v: int32) -> None:
        self.val = v


@nocopy
class Pin:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Holder:
    box: Box
    pair: tuple[int32, Pin]

    def __init__(self, v: int32) -> None:
        self.box = Box(v)
        self.pair = (v, Pin(v))


class BoxPair:
    pair: tuple[int32, Box]

    def __init__(self, v: int32) -> None:
        self.pair = (v, Box(v))


class Ctx:
    def __enter__(self) -> int32:
        return 0

    def __exit__(self, et, ev, tb) -> None:
        pass


@error_return(Bad)
def decode(n: int32) -> Own[Box]:
    if n > 0:
        return Box(n)
    raise Bad


def for_const_tuple(h: Holder) -> int32:
    # A borrow tuple a for statement hoists, every source const: the
    # predecl spells `const Pin*` (Pin is @nocopy, so a copy cannot build).
    for _i in range(1):
        t = h.pair  # tpyc: ok
    return t[0] + t[1].n


def try_error_return(h: Holder) -> None:
    try:
        b = h.box  # tpyc: ok
        b.val += 1
        b = decode(5)  # tpyc: ok
    except Bad:
        return
    alias = b
    alias.val += 10
    print("try_error_return", h.box.val, b.val)


def while_error_return(h: Holder) -> None:
    n = 0
    while n < 2:
        b = h.box  # tpyc: ok
        b.val += 1
        try:
            b = decode(5 + n)  # tpyc: ok
        except Bad:
            return
        n += 1
    alias = b
    alias.val += 10
    print("while_error_return", h.box.val, b.val)


def for_error_return(h: Holder) -> None:
    for i in range(2):
        b = h.box  # tpyc: ok
        b.val += 1
        try:
            b = decode(10 + i)  # tpyc: ok
        except Bad:
            return
    alias = b
    alias.val += 100
    print("for_error_return", h.box.val, b.val)


def with_error_return(h: Holder) -> None:
    with Ctx():
        b = h.box  # tpyc: ok
        b.val += 1
        try:
            b = decode(5)  # tpyc: ok
        except Bad:
            return
    alias = b
    alias.val += 10
    print("with_error_return", h.box.val, b.val)


def while_reseat(a: Box, n: int32) -> None:
    while True:
        x = Box(n)  # tpyc: ok
        break
    y = x
    x = a  # the alias keeps the first object across the reseat
    y.val += 1
    x.val += 5
    x = Box(99)  # a second rvalue reseat; y still holds the first object
    y.val += 1
    print("while_reseat", y.val, a.val, x.val)


def try_reseat(a: Box, n: int32) -> None:
    try:
        x = Box(n)  # tpyc: ok
    except ValueError:
        x = Box(0)
    y = x
    x = a
    y.val += 1
    x.val += 5
    x = Box(99)  # a second rvalue reseat; y still holds the first object
    y.val += 1
    print("try_reseat", y.val, a.val, x.val)


def with_reseat(a: Box, n: int32) -> None:
    with Ctx():
        x = Box(n)  # tpyc: ok
    y = x
    x = a
    y.val += 1
    x.val += 5
    x = Box(99)  # a second rvalue reseat; y still holds the first object
    y.val += 1
    print("with_reseat", y.val, a.val, x.val)


def for_reseat(a: Box, n: int32) -> None:
    for _i in range(1):
        x = Box(n)  # tpyc: ok
    y = x
    x = a
    y.val += 1
    x.val += 5
    x = Box(99)  # a second rvalue reseat; y still holds the first object
    y.val += 1
    print("for_reseat", y.val, a.val, x.val)


def make_box(v: int32) -> Own[Box]:
    return Box(v)


# A nullable borrow tuple bound inside each statement: the predecl is the
# `std::optional` of the pointer tuple, so a write through it after the
# statement reaches the holder's own Pin.
def nbt_while(h: Holder) -> None:
    while True:
        t: tuple[int32, Pin] | None = h.pair  # tpyc: ok
        break
    if t is not None:
        t[1].n += 1
    print("nbt_while", h.pair[1].n)


def nbt_with(h: Holder) -> None:
    with Ctx():
        t: tuple[int32, Pin] | None = h.pair  # tpyc: ok
    if t is not None:
        t[1].n += 1
    print("nbt_with", h.pair[1].n)


def nbt_try_except(h: Holder) -> None:
    try:
        t: tuple[int32, Pin] | None = h.pair  # tpyc: ok
    except ValueError:
        t = None
    if t is not None:
        t[1].n += 1
    print("nbt_try_except", h.pair[1].n)


def nbt_try_finally(h: Holder) -> None:
    try:
        t: tuple[int32, Pin] | None = h.pair  # tpyc: ok
    finally:
        pass
    if t is not None:
        t[1].n += 1
    print("nbt_try_finally", h.pair[1].n)


def nbt_for(h: Holder) -> None:
    for _i in range(2):
        t: tuple[int32, Pin] | None = h.pair  # tpyc: ok
    if t is not None:
        t[1].n += 1
    print("nbt_for", h.pair[1].n)


def nbt_if_in_loop(h: Holder) -> None:
    for i in range(2):
        if i == 0:
            t: tuple[int32, Pin] | None = h.pair  # tpyc: ok
        else:
            t = None
        if t is not None:
            t[1].n += 1
    print("nbt_if_in_loop", h.pair[1].n)


# A borrow tuple bound inside a while / with: the predecl holds the pointer.
def bt_while(h: Holder) -> None:
    while True:
        t = h.pair  # tpyc: ok
        break
    t[1].n += 1
    print("bt_while", h.pair[1].n)


def bt_with(h: Holder) -> None:
    with Ctx():
        t = h.pair  # tpyc: ok
    t[1].n += 1
    print("bt_with", h.pair[1].n)


def pick(b: readonly[Box]) -> readonly[Box]:
    return b


# A const borrow re-seated by a call inside each statement: the const
# pointer points at the call's result, which is the argument itself, so a
# write through the argument's source shows through it.
def cr_try(a: readonly[Box], src: Box) -> None:
    try:
        v = a
        v = pick(src)  # tpyc: ok
    except ValueError:
        v = a
    src.val += 1
    print("cr_try", v.val)


def cr_while(a: readonly[Box], src: Box) -> None:
    while True:
        v = a
        v = pick(src)  # tpyc: ok
        break
    src.val += 1
    print("cr_while", v.val)


def cr_with(a: readonly[Box], src: Box) -> None:
    with Ctx():
        v = a
        v = pick(src)  # tpyc: ok
    src.val += 1
    print("cr_with", v.val)


def cr_for(a: readonly[Box], src: Box) -> None:
    for _i in range(2):
        v = a
        v = pick(src)  # tpyc: ok
    src.val += 1
    print("cr_for", v.val)


def make_pair(n: int32) -> Own[tuple[int32, Box]]:
    return (n, Box(n))


# A walrus-bound owned tuple rebound in the statement: the walrus fills one
# function-top slot, and the rebind re-points the name at the holder's pair.
def wal_for(h: BoxPair) -> None:
    first = 0
    for i in range(2):
        first = (t := make_pair(9 + i))[1].val  # tpyc: ok
        t = h.pair
    t[1].val += 1
    print("wal_for", first, h.pair[1].val)


def wal_try(h: BoxPair) -> None:
    try:
        first = (t := make_pair(9))[1].val  # tpyc: ok
        t = h.pair
    except ValueError:
        return
    t[1].val += 1
    print("wal_try", first, h.pair[1].val)


def wal_with(h: BoxPair) -> None:
    with Ctx():
        first = (t := make_pair(9))[1].val  # tpyc: ok
        t = h.pair
    t[1].val += 1
    print("wal_with", first, h.pair[1].val)


def take_opt(x: Box | None) -> int32:
    if x is not None:
        x.val += 100
        return x.val
    return -1


# A local bound inside each statement is the object its predecl holds: a
# tuple element, a `Box | None` element or argument, a reseat or a first
# binding of a `Box | None` local takes that object's address, so every
# write through one of them shows through `b`.
def addr_for(n: int32) -> None:
    q: Box | None = None
    for _i in range(1):
        b = Box(n)
        t = (n, b)  # tpyc: ok
        o: tuple[int32, Box | None] = (n, b)  # tpyc: ok
        r = take_opt(b)  # tpyc: ok
        # The loop-alias warning (BUGS.md#loop-rebind-site-slot-reuse-warned);
        # the loop runs one pass, so the output matches CPython.
        q = b  # tpyc: warning(/will not keep the object/)
    p: Box | None = b  # tpyc: ok
    t[1].val += 1
    e = o[1]
    if e is not None and q is not None and p is not None:
        e.val += 1
        q.val += 1
        p.val += 1
    print("addr_for", r, b.val)


def addr_while(n: int32) -> None:
    q: Box | None = None
    while True:
        b = Box(n)
        t = (n, b)  # tpyc: ok
        o: tuple[int32, Box | None] = (n, b)  # tpyc: ok
        r = take_opt(b)  # tpyc: ok
        # The loop-alias warning (BUGS.md#loop-rebind-site-slot-reuse-warned);
        # the loop runs one pass, so the output matches CPython.
        q = b  # tpyc: warning(/will not keep the object/)
        break
    p: Box | None = b  # tpyc: ok
    t[1].val += 1
    e = o[1]
    if e is not None and q is not None and p is not None:
        e.val += 1
        q.val += 1
        p.val += 1
    print("addr_while", r, b.val)


def addr_with(n: int32) -> None:
    q: Box | None = None
    with Ctx():
        b = Box(n)
        t = (n, b)  # tpyc: ok
        o: tuple[int32, Box | None] = (n, b)  # tpyc: ok
        r = take_opt(b)  # tpyc: ok
        q = b  # tpyc: ok
    p: Box | None = b  # tpyc: ok
    t[1].val += 1
    e = o[1]
    if e is not None and q is not None and p is not None:
        e.val += 1
        q.val += 1
        p.val += 1
    print("addr_with", r, b.val)


def addr_try(n: int32) -> None:
    q: Box | None = None
    try:
        b = Box(n)
        t = (n, b)  # tpyc: ok
        o: tuple[int32, Box | None] = (n, b)  # tpyc: ok
        r = take_opt(b)  # tpyc: ok
        q = b  # tpyc: ok
    finally:
        pass
    p: Box | None = b  # tpyc: ok
    t[1].val += 1
    e = o[1]
    if e is not None and q is not None and p is not None:
        e.val += 1
        q.val += 1
        p.val += 1
    print("addr_try", r, b.val)


def addr_if(n: int32) -> None:
    # An owned slot an `if` hoists, addressed in a tuple literal.
    if n > 0:
        b = Box(n)
        t = (n, b)  # tpyc: ok
    else:
        return
    t[1].val += 1
    print("addr_if", b.val)


def own_opt_elem(o: Own[Box | None], n: int32) -> None:  # tpyc: warning(/never consumed/)
    # An `Own[Box | None]` parameter may be empty: the element is its
    # pointer (None when it is), never the object addressed.
    t: tuple[Box | None, int32] = (o, n)  # tpyc: ok
    e = t[0]
    if e is not None:
        e.val += 1
    v = -1
    if o is not None:
        v = o.val
    print("own_opt_elem", e is None, n, v)


def ret_opt(n: int32) -> Own[Box | None]:
    # An owned slot returned into an `Own[Box | None]` result: its last use
    # moves it.
    for _i in range(1):
        b = Box(n)
    return b  # tpyc: ok


class OptHolder:
    opt: Box | None

    def __init__(self) -> None:
        self.opt = None


def field_opt(h: OptHolder, n: int32) -> None:
    for _i in range(1):
        b = Box(n)
    # An owned slot stored into a `Box | None` field at its last use moves.
    h.opt = b  # tpyc: ok
    if h.opt is not None:
        h.opt.val += 1
    w = h.opt
    if w is not None:
        print("field_opt", w.val)


def field_opt_kept(h: OptHolder, n: int32) -> None:
    for _i in range(1):
        b = Box(n)
    # Read again after the store: the field takes a copy, as the walrus's
    # owned slot's does (only read after, so the copy matches CPython).
    h.opt = b  # tpyc: warning(/copies Box into field/)
    w = h.opt
    if w is not None:
        print("field_opt_kept", w.val, b.val)


def storage_opt_elem(opts: list[Box | None]) -> None:
    # A storage-optional loop variable may be empty: the element is its
    # pointer (None when it is), as when it is passed to a `Box | None`
    # parameter.
    n = 0
    for p in opts:
        n += 1
        t: tuple[int32, Box | None] = (n, p)  # tpyc: ok
        e = t[1]
        if e is not None:
            e.val += 1
        print("storage_opt_elem", n, e is None)


class Walker:
    pair: tuple[int32, Pin]

    def __init__(self, v: int32) -> None:
        self.pair = (v, Pin(v))

    def nbt_while(self) -> None:
        # Method body: a nullable borrow tuple bound inside a while.
        while True:
            t: tuple[int32, Pin] | None = self.pair  # tpyc: ok
            break
        if t is not None:
            t[1].n += 1
        print("method_nbt_while", self.pair[1].n)


def closure_bt_with(h: Holder) -> None:
    def inner(g: Holder) -> None:
        # Closure body: a borrow tuple bound inside a with.
        with Ctx():
            t = g.pair  # tpyc: ok
        t[1].n += 1
    inner(h)
    print("closure_bt_with", h.pair[1].n)


def gen_addr(n: int32) -> Iterator[int32]:
    # Generator: a loop-head owned slot, addressed after the loop in a
    # tuple literal and as a `Box | None` argument.
    for _i in range(1):
        b = Box(n)
    t = (n, b)  # tpyc: ok
    r = take_opt(b)  # tpyc: ok
    yield r
    t[1].val += 1
    yield b.val


def main() -> None:
    print("for_const_tuple", for_const_tuple(Holder(3)))
    try_error_return(Holder(1))
    while_error_return(Holder(1))
    for_error_return(Holder(1))
    with_error_return(Holder(1))
    a = Box(0)
    while_reseat(a, 1)
    try_reseat(a, 1)
    with_reseat(a, 1)
    for_reseat(a, 1)
    h = Holder(1)
    nbt_while(h)
    nbt_with(h)
    nbt_try_except(h)
    nbt_try_finally(h)
    nbt_for(h)
    nbt_if_in_loop(h)
    bt_while(h)
    bt_with(h)
    src = Box(20)
    cr_try(a, src)
    cr_while(a, src)
    cr_with(a, src)
    cr_for(a, src)
    wal_for(BoxPair(1))
    wal_try(BoxPair(1))
    wal_with(BoxPair(1))
    addr_for(1)
    addr_while(1)
    addr_with(1)
    addr_try(1)
    addr_if(1)
    own_opt_elem(Box(3), 1)
    own_opt_elem(None, 2)
    x = ret_opt(5)
    if x is not None:
        x.val += 1
        print("ret_opt", x.val)
    field_opt(OptHolder(), 7)
    field_opt_kept(OptHolder(), 7)
    opts: list[Box | None] = [Box(4), None]
    storage_opt_elem(opts)
    for o in opts:
        if o is not None:
            print("storage_opt_elem after", o.val)
    Walker(1).nbt_while()
    closure_bt_with(Holder(1))
    for v in gen_addr(3):
        print("gen_addr", v)


main()

# Module level: a pointer global rebound from an rvalue inside a `try`
# fills a `static` slot, so the object outlives `__tpy_init`.
mod_src = Box(100)
try:
    mod_b = mod_src  # tpyc: ok
    mod_b = make_box(9)  # tpyc: ok
except ValueError:
    mod_b = mod_src
mod_alias = mod_b
mod_alias.val += 1
print("module_try", mod_b.val, mod_src.val)
