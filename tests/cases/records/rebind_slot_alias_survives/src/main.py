# A rebind of a reference local takes a slot of its own, declared right
# before the function-body-level statement that contains the rebind. These
# cover both sides of that:
#   - alias_survives_rebind / rebind_in_loop: the slot is separate storage
#     from the init slot -- an alias taken before the rebind keeps pointing
#     at the ORIGINAL value and observes mutation there.
#   - borrow_call_rebind / none_reassign: the reassignment needs no slot (a
#     borrow-returning call takes an address; None is a null pointer), so no
#     dead `std::optional<T>` may appear in the generated C++.
#   - scalar_deep .. getitem_escape: what reaches the old object by a path no
#     loan records (two hops in, a view, a `with` manager, a pointer a
#     constructor, setter or `__getitem__` handed out) still reads it.
#   - *_root: a holder two hops into a rebound list or dict local (a nested
#     list, a record field's list) keeps the old object, in a plain function
#     and a generator; each mutates it after the rebind.
#   - hoisted_*: a rebind in a loop body takes its slot before the loop
#     statement, so an outer holder of the rebound object keeps it after the
#     loop (a `for` body, an `if` in one, a method, a rebind in a nested
#     `if`); each prints both names, which still alias.
#   - block_*: a rebind inside a block -- of a name first bound behind a
#     pointer predeclaration (a nested `if`, a `try` arm in a loop, an
#     `@error_return` bind, a `finally` emitted on two paths, an
#     `@error_return` rebind two blocks in), in the arm that first bound
#     the name (arm_rebind), of a name first bound to a borrow (an unpack
#     target off a call that lends it, unpack_borrow; a plain name,
#     borrow_first), of a name first bound inside the block with an alias
#     taken before the rebind (alias_rebind) or off an owning unpack
#     (unpack_own) -- takes its slot before the outer statement holding the
#     block, so an outer holder read after the block still reads the
#     object. A list built after each block reuses the stack the block
#     left.
#   - arm_alias_rebind_*: alias_rebind's shape in a `try` arm, a `match`
#     arm, an `else` arm and over an Optional local.
#   - slot_drop_*: a rebind's slot is destroyed before what its object views
#     (plain function, generator, coroutine, a generator's unpack and walrus
#     sites, a plain function's unpack target held by the unpack, a view of
#     a local declared before the first binding (view_before) or between
#     it and the rebind (view_after), a rebind of a borrowed unpack target
#     (borrow_unpack)); the drops are read sorted after the function
#     returns, since when an object drops is no part of Python's semantics.
import asyncio
from typing import Iterator
from tpy import (Own, Ptr, ReturnException, StrView, error_return, int32,
                 readonly, take_ptr)


class Counter:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def bump(self) -> None:
        self.n += 1


def pick(c: Counter) -> Counter:
    return c


def alias_survives_rebind() -> None:
    c = Counter(1)
    alias = c
    c = Counter(10)  # tpyc: ok
    alias.bump()
    print("alias:", alias.n, "c:", c.n)


def rebind_in_loop() -> None:
    c = Counter(0)
    first = c
    i = 0
    while i < 3:
        c = Counter(i)  # tpyc: ok
        i += 1
    first.bump()
    print("first:", first.n, "c:", c.n)


def borrow_call_rebind(seed: Counter) -> None:
    result = seed
    i = 0
    while i < 2:
        result = pick(seed)
        i += 1
    result.bump()
    print("seed:", seed.n, "result:", result.n)


def none_reassign(flag: bool) -> None:
    c: Counter | None = Counter(5)
    if flag:
        c = None
    print("none:" if c is None else "some:", 0 if c is None else c.n)


class Inner:
    cs: list[Counter]

    def __init__(self, i: int32) -> None:
        self.cs = [Counter(i), Counter(i + 1)]


class Deep:
    inner: Inner

    def __init__(self, i: int32) -> None:
        self.inner = Inner(i)


class Viewing:
    s: str
    v: StrView

    def __init__(self, n: int32, v: StrView) -> None:
        self.s = "abcdefghijklmnopqrstuvwxyz0123456789" + str(n)
        self.v = v


class Managed:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __enter__(self) -> "Managed":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        print("with-scalar exit", self.n)


class Keeper:
    p: Ptr[Counter]

    def __init__(self, c: Counter) -> None:
        self.p = take_ptr(c)

    def save(self, c: Counter) -> None:
        self.p = take_ptr(c)


class Escaping:
    c: Counter
    k: Ptr[Keeper]

    def __init__(self, n: int32, k: Keeper) -> None:
        self.c = Counter(n)
        self.k = take_ptr(k)
        if n == 1:
            # The constructor hands out the address of its own field.
            self.k.save(self.c)

    @property
    def value(self) -> int32:
        return self.c.n

    @value.setter
    def value(self, v: int32) -> None:
        self.k.save(self.c)

    @readonly(False)
    def __getitem__(self, i: int32) -> Counter:
        self.k.save(self.c)
        return self.c


# Each rebind below keeps the old object in a slot of its own; what still
# reaches it -- a path two hops in, a view, a `with` manager, a pointer a
# constructor, a setter or a `__getitem__` handed out -- reads it unchanged.
def scalar_deep(c: bool) -> None:
    t = Deep(1)
    # scalar-deep: two hops into the record.
    saved = t.inner.cs[1]
    if c:
        t = Deep(10)  # tpyc: ok
    saved.bump()
    print("scalar-deep", saved.n, t.inner.cs[1].n)


def self_view(c: bool) -> None:
    t = Viewing(1, "start")
    if c:
        # self-view: the new object views the old one's string.
        t = Viewing(10, t.s)  # tpyc: ok
    print("self-view", t.v)


def with_scalar(c: bool) -> None:
    t = Managed(1)
    # with-scalar: the manager's exit runs on the object the `with` entered.
    with t:
        if c:
            t = Managed(10)  # tpyc: ok
        print("with-scalar body", t.n)


def constructor_escape(c: bool) -> None:
    k = Keeper(Counter(99))
    t = Escaping(1, k)
    if c:
        # constructor-escape: the old object's field is still pointed at.
        t = Escaping(10, k)  # tpyc: ok
    k.p.bump()
    print("constructor-escape", k.p.n, t.c.n)


def setter_escape(c: bool) -> None:
    k = Keeper(Counter(99))
    t = Escaping(2, k)
    t.value = 3
    if c:
        # setter-escape: the setter handed out the old object's field.
        t = Escaping(10, k)  # tpyc: ok
    k.p.bump()
    print("setter-escape", k.p.n, t.c.n)


def getitem_escape(c: bool) -> None:
    k = Keeper(Counter(99))
    t = Escaping(2, k)
    t[0].n = 3
    if c:
        # getitem-escape: `__getitem__` handed out the old object's field.
        t = Escaping(10, k)  # tpyc: ok
    k.p.bump()
    print("getitem-escape", k.p.n, t.c.n)


class A:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class HA:
    xs: list[A]

    def __init__(self, i: int32) -> None:
        self.xs = [A(i), A(i + 1)]


def nested_list_root(c: bool) -> None:
    xs = [[A(1), A(2)]]
    # nested-list-root: the holder two hops into the list keeps the old A.
    saved = xs[0][1]
    if c:
        xs = [[A(9), A(8)]]  # tpyc: ok
    saved.x += 100
    print("nested-list-root", saved.x, xs[0][1].x)


def gen_nested_list_root(c: bool) -> Iterator[int32]:
    xs = [[A(1), A(2)]]
    # gen-nested-list-root: the same across a suspension.
    saved = xs[0][1]
    yield saved.x
    if c:
        xs = [[A(9), A(8)]]  # tpyc: ok
    saved.x += 100
    yield saved.x
    yield xs[0][1].x


def list_root(c: bool) -> None:
    xs = [HA(1), HA(2)]
    xs.append(HA(3))
    # list-root: a record field's element, reached through the list.
    saved = xs[0].xs[1]
    if c:
        xs = [HA(20)]  # tpyc: ok
    saved.x += 75
    print("list-root", saved.x, xs[0].xs[1].x)


def gen_list_root(c: bool) -> Iterator[int32]:
    xs = [HA(1), HA(2)]
    xs.append(HA(3))
    # gen-list-root: the same across a suspension.
    saved = xs[0].xs[1]
    yield saved.x
    if c:
        xs = [HA(20)]  # tpyc: ok
    saved.x += 75
    yield saved.x
    yield xs[0].xs[1].x


def dict_root(c: bool) -> None:
    d = {1: HA(1)}
    # dict-root: a record field's element, reached through the dict.
    saved = d[1].xs[1]
    if c:
        d = {1: HA(20)}  # tpyc: ok
    saved.x += 75
    print("dict-root", saved.x, d[1].xs[1].x)


def gen_dict_root(c: bool) -> Iterator[int32]:
    d = {1: HA(1)}
    # gen-dict-root: the same across a suspension.
    saved = d[1].xs[1]
    yield saved.x
    if c:
        d = {1: HA(20)}  # tpyc: ok
    saved.x += 75
    yield saved.x
    yield d[1].xs[1].x


# hoisted-loop: `keep` outlives the loop that rebinds `p`.
def hoisted_loop() -> None:
    keep = Counter(0)
    for i in range(1):
        p = Counter(i)
        p = Counter(i + 3)  # tpyc: ok
        # A conservative warning: no later pass reads the old object
        # (BUGS.md#loop-slot-reuse-false-warning).
        keep = p  # tpyc: warning(/'keep' will not keep the object/)
    keep.bump()
    print("hoisted-loop", keep.n, p.n)


# hoisted-if: the holder is taken in an `if` inside the loop body.
def hoisted_if() -> None:
    keep = Counter(0)
    for i in range(3):
        p = Counter(i)
        p = Counter(i + 3)  # tpyc: ok
        if i == 2:
            # Conservative (BUGS.md#loop-slot-reuse-false-warning).
            keep = p  # tpyc: warning(/'keep' will not keep the object/)
            break
    keep.bump()
    print("hoisted-if", keep.n, p.n)


class Looper:
    def __init__(self) -> None:
        pass

    # hoisted-method: the same loop in a method body.
    def run(self) -> None:
        keep = Counter(0)
        for i in range(1):
            p = Counter(i)
            p = Counter(i + 7)  # tpyc: ok
            # Conservative (BUGS.md#loop-slot-reuse-false-warning).
            keep = p  # tpyc: warning(/'keep' will not keep the object/)
        keep.bump()
        print("hoisted-method", keep.n, p.n)


# hoisted-nested-if: the rebind sits in an `if` inside the loop body; the
# next pass's first binding refills the first storage, not the rebind's.
def hoisted_nested_if() -> None:
    keep = Counter(0)
    for i in range(3):
        p = Counter(i)
        if i == 1:
            p = Counter(i + 10)  # tpyc: ok
            # Conservative (BUGS.md#loop-slot-reuse-false-warning).
            keep = p  # tpyc: warning(/'keep' will not keep the object/)
    keep.bump()
    print("hoisted-nested-if", keep.n, p.n)


class Fail(Exception, ReturnException):
    pass


@error_return(Fail)
def make_counter(i: int32) -> Own[Counter]:
    if i < 0:
        raise Fail()
    return Counter(i)


# block-nested-if: the first binding sits in an inner `if`, behind the
# outer block's predeclaration of `p`.
def block_nested_if(d: bool) -> None:
    keep = Counter(0)
    if keep.n == 0:
        if d:
            p = Counter(1)  # tpyc: ok
        else:
            p = Counter(2)  # tpyc: ok
        keep = p
        p = Counter(5)  # tpyc: ok
        keep.bump()
        print("block-nested-if in", keep.n, p.n)
    junk = [Counter(70), Counter(80), Counter(90)]
    keep.bump()
    print("block-nested-if", keep.n, len(junk))


# block-try-loop: the first binding sits in a `try` arm of a loop body.
def block_try_loop() -> None:
    keep = Counter(0)
    for i in range(2):
        try:
            p = Counter(i + 1)  # tpyc: ok
        except ValueError:
            p = Counter(9)
        # Conservative (BUGS.md#loop-slot-reuse-false-warning).
        keep = p  # tpyc: warning(/'keep' will not keep the object/)
        print("block-try-loop in", keep.n, p.n)
    junk = [Counter(70), Counter(80), Counter(90)]
    keep.bump()
    print("block-try-loop", keep.n, len(junk))


# block-er: an `@error_return` first binding and rebind in an `if` block.
@error_return(Fail)
def block_er(c: bool) -> int32:
    keep = Counter(0)
    if c:
        p = make_counter(1)  # tpyc: ok
        keep = p
        p = make_counter(20)  # tpyc: ok
        keep.bump()
        print("block-er in", keep.n, p.n)
    junk = [Counter(70), Counter(80), Counter(90)]
    keep.bump()
    return keep.n * 10 + len(junk)


# block-finally: the rebind sits in a `finally` emitted on the `continue`
# path and the fall-through path, both filling one slot.
def block_finally(c: bool) -> None:
    keep = Counter(0)
    for i in range(2):
        try:
            p = Counter(10 + i)
            if c:
                continue
        finally:
            # Conservative (BUGS.md#loop-slot-reuse-false-warning).
            keep = p  # tpyc: warning(/'keep' will not keep the object/)
            p = Counter(20 + i)  # tpyc: ok
            keep.bump()
        print("block-finally body", keep.n, p.n)
    junk = [Counter(70), Counter(80), Counter(90)]
    keep.bump()
    print("block-finally", c, keep.n, len(junk))


# block-arm-rebind: the arm that first binds `p` rebinds it; `keep` holds
# the first object after the `if`.
def block_arm_rebind(c: bool) -> None:
    keep = Counter(0)
    if c:
        p = Counter(1)
        keep = p
        p = Counter(2)  # tpyc: ok
    else:
        p = Counter(3)
    junk = [Counter(70), Counter(80), Counter(90)]
    keep.bump()
    print("block-arm-rebind", keep.n, p.n, len(junk))


# block-er-nested: an `@error_return` first binding and rebind two blocks
# in.
@error_return(Fail)
def block_er_nested(n: int32) -> int32:
    keep = Counter(0)
    if n > 0:
        if n > 1:
            p = make_counter(n)  # tpyc: ok
            keep = p
            p = make_counter(n + 10)  # tpyc: ok
            print("block-er-nested in", keep.n, p.n)
    junk = [Counter(70), Counter(80), Counter(90)]
    keep.bump()
    return keep.n * 10 + len(junk)


def lend_counter(c: Counter) -> tuple[Counter, int32]:
    return (c, 7)


# block-unpack-borrow: `x` is first bound to the counter the call lends, in
# an `if` block; the rebind's slot is declared before the `if`, so `keep`
# still reads it after the block.
def block_unpack_borrow(b: Counter, c: bool) -> None:
    keep = Counter(0)
    if c:
        x, k = lend_counter(b)
        x = Counter(5)  # tpyc: ok
        keep = x
        print("block-unpack-borrow in", x.n, k)
    junk = [Counter(70), Counter(80), Counter(90)]
    keep.bump()
    print("block-unpack-borrow", keep.n, b.n, len(junk))


# block-borrow-first: the same with `p` first bound to a parameter.
def block_borrow_first(seed: Counter, c: bool) -> None:
    keep = Counter(0)
    if c:
        p = seed
        p = Counter(2)  # tpyc: ok
        keep = p
        print("block-borrow-first in", p.n)
    junk = [Counter(70), Counter(80), Counter(90)]
    keep.bump()
    print("block-borrow-first", keep.n, seed.n, len(junk))


class Tagged:
    s: str
    n: int32

    def __init__(self, n: int32) -> None:
        self.s = "tagged-object-with-a-long-heap-string-" + str(n)
        self.n = n

    def bump(self) -> None:
        self.n += 1


# block-alias-rebind: `p` is first bound in the `if` and aliased before its
# rebind; the rebind's slot is declared before the `if`, so `keep` still
# reads the rebound object after the block.
def block_alias_rebind(c: bool) -> None:
    keep = Tagged(0)
    if c:
        p = Tagged(1)
        q = p
        p = Tagged(5)  # tpyc: ok
        keep = p
        q.bump()
        print("block-alias-rebind in", q.n, p.n)
    junk = [Tagged(70), Tagged(80), Tagged(90)]
    keep.bump()
    print("block-alias-rebind", keep.s, keep.n, len(junk))


# arm-alias-rebind-try: the same in a `try` arm.
def arm_alias_rebind_try() -> None:
    keep = Counter(0)
    try:
        p = Counter(1)
        q = p
        p = Counter(5)  # tpyc: ok
        keep = p
        q.bump()
    except ValueError:
        pass
    junk = [Counter(70), Counter(80), Counter(90)]
    keep.bump()
    print("arm-alias-rebind-try", keep.n, len(junk))


# arm-alias-rebind-match: the same in a `match` arm.
def arm_alias_rebind_match(c: bool) -> None:
    keep = Counter(0)
    match c:
        case True:
            p = Counter(1)
            q = p
            p = Counter(5)  # tpyc: ok
            keep = p
            q.bump()
        case _:
            pass
    junk = [Counter(70), Counter(80), Counter(90)]
    keep.bump()
    print("arm-alias-rebind-match", keep.n, len(junk))


# arm-alias-rebind-else: the same in an `else` arm.
def arm_alias_rebind_else(c: bool) -> None:
    keep = Counter(0)
    if not c:
        pass
    else:
        p = Counter(1)
        q = p
        p = Counter(5)  # tpyc: ok
        keep = p
        q.bump()
    junk = [Counter(70), Counter(80), Counter(90)]
    keep.bump()
    print("arm-alias-rebind-else", keep.n, len(junk))


# arm-alias-rebind-optional: the same over an Optional local.
def arm_alias_rebind_optional(c: bool) -> None:
    keep = Counter(0)
    if c:
        p: Counter | None = Counter(1)
        q = p
        p = Counter(5)  # tpyc: ok
        keep = p
        q.bump()
    junk = [Counter(70), Counter(80), Counter(90)]
    keep.bump()
    print("arm-alias-rebind-optional", keep.n, len(junk))


def make_tagged_pair(i: int32) -> Own[tuple[Tagged, int32]]:
    return (Tagged(i), i)


# block-unpack-own: `p` is first bound off an owning unpack in the `if`;
# its rebind's slot is declared before the `if`.
def block_unpack_own(c: bool) -> None:
    out = Tagged(0)
    if c:
        p, k = make_tagged_pair(1)
        p = Tagged(2)  # tpyc: ok
        out = p
        print("block-unpack-own in", p.n, k)
    junk = [Tagged(70), Tagged(80), Tagged(90)]
    out.bump()
    print("block-unpack-own", out.s, out.n, len(junk))


VIEW_DROPS: list[str] = []


class Src:
    s: str

    def __init__(self, n: int32) -> None:
        self.s = "abcdefghijklmnopqrstuvwxyz0123456789-" + str(n)


class Viewer:
    v: StrView

    def __init__(self, v: StrView) -> None:
        self.v = v

    def __del__(self) -> None:
        # Reads the viewed string: its storage must still be alive.
        VIEW_DROPS.append(str(self.v))


def view_drops() -> Own[list[str]]:
    out = sorted(VIEW_DROPS)
    VIEW_DROPS.clear()
    return out


def slot_drop_plain() -> None:
    a = Src(1)
    w = Viewer(a.s)
    # slot-drop-plain: the rebind's slot dies before `a`, declared after the
    # function's other locals were.
    w = Viewer(a.s)  # tpyc: ok
    print("slot-drop-plain", w.v)


def slot_drop_gen(c: bool) -> Iterator[int32]:
    p = Src(1)
    if c:
        # slot-drop-gen: the frame field of this rebind dies before `w`'s
        # view into it.
        p = Src(2)  # tpyc: ok
    w = Viewer(p.s)
    yield len(w.v)


async def slot_drop_async(c: bool) -> int32:
    p = Src(1)
    if c:
        # slot-drop-async: the same, across a suspension.
        p = Src(3)  # tpyc: ok
    w = Viewer(p.s)
    await asyncio.sleep(0)
    return len(w.v)


async def slot_drop_async_main() -> None:
    print("slot-drop-async", await slot_drop_async(True))


def make_src(n: int32) -> tuple[Own[Src], int32]:
    return (Src(n), n)


def make_srcs(n: int32) -> Own[tuple[Src, Src]]:
    return (Src(n), Src(n + 1))


def slot_drop_unpack_plain() -> None:
    p, q = make_srcs(7)
    w = Viewer(p.s)
    # slot-drop-unpack-plain: `p`'s first object lives in the unpack's
    # holder, and this rebind's slot is declared after `w`'s first storage,
    # whose object views the holder's; the slot dies first.
    p = Src(9)  # tpyc: ok
    w = Viewer(p.s)
    print("slot-drop-unpack-plain", w.v, len(p.s), q.s)


def slot_drop_unpack_gen(c: bool) -> Iterator[int32]:
    p, k = make_src(1)
    if c:
        # slot-drop-unpack-gen: an unpack site's frame field follows `p`, so
        # it dies before `w`'s view into it.
        p, k = make_src(4)  # tpyc: ok
    w = Viewer(p.s)
    yield len(w.v) + k


def slot_drop_walrus_gen() -> Iterator[int32]:
    # slot-drop-walrus-gen: the walrus site's field and the rebind's follow
    # `p`, so they die before `w`'s view into the rebind's object.
    if (p := Src(5)).s != "":  # tpyc: ok
        yield len(p.s)
    p = Src(6)  # tpyc: ok
    w = Viewer(p.s)
    yield len(w.v)


def slot_drop_view_before(n: int32) -> None:
    s = "abcdefghijklmnopqrstuvwxyz" * n
    w = Viewer("first-literal")
    # slot-drop-view-before: the rebind's slot is declared after `s`, so it
    # dies before the string its object views.
    w = Viewer(s)  # tpyc: ok
    print("slot-drop-view-before", len(w.v))


def slot_drop_view_after(n: int32) -> None:
    w = Viewer("init")
    s = "abcdefghijklmnopqrstuvwxyz0123456789-" * n
    # slot-drop-view-after: `s` is declared after `w`'s first storage but
    # before this statement, which the rebind's slot is declared right
    # before, so the slot still dies before `s`.
    w = Viewer(s)  # tpyc: ok
    print("slot-drop-view-after", len(w.v))


def lend_viewer(w: Viewer) -> tuple[Viewer, int32]:
    return (w, 7)


def slot_drop_borrow_unpack(w0: Viewer) -> None:
    a = Src(1)
    x, k = lend_viewer(w0)
    # slot-drop-borrow-unpack: `x` first borrows `w0`; the rebind's slot is
    # declared after `a`, so it dies before the string its object views.
    x = Viewer(a.s)  # tpyc: ok
    print("slot-drop-borrow-unpack", len(x.v), k)


def slot_drop_borrow_unpack_main() -> None:
    w0 = Viewer("w0-zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz")
    slot_drop_borrow_unpack(w0)


def main() -> None:
    alias_survives_rebind()
    rebind_in_loop()
    borrow_call_rebind(Counter(7))
    none_reassign(True)
    none_reassign(False)
    scalar_deep(True)
    self_view(True)
    with_scalar(True)
    constructor_escape(True)
    setter_escape(True)
    getitem_escape(True)
    nested_list_root(True)
    print("gen-nested-list-root", list(gen_nested_list_root(True)))
    list_root(True)
    print("gen-list-root", list(gen_list_root(True)))
    dict_root(True)
    print("gen-dict-root", list(gen_dict_root(True)))
    hoisted_loop()
    hoisted_if()
    Looper().run()
    hoisted_nested_if()
    block_nested_if(True)
    block_nested_if(False)
    block_try_loop()
    try:
        r = block_er(True)
        print("block-er", r)
        r = block_er_nested(3)
        print("block-er-nested", r)
    except Fail:
        print("fail")
    block_finally(False)
    block_finally(True)
    block_arm_rebind(True)
    block_arm_rebind(False)
    block_unpack_borrow(Counter(1), True)
    block_borrow_first(Counter(1), True)
    block_alias_rebind(True)
    arm_alias_rebind_try()
    arm_alias_rebind_match(True)
    arm_alias_rebind_else(True)
    arm_alias_rebind_optional(True)
    block_unpack_own(True)
    slot_drop_plain()
    print("slot-drop-plain dropped:", view_drops())
    for n in slot_drop_gen(True):
        print("slot-drop-gen", n)
    print("slot-drop-gen dropped:", view_drops())
    asyncio.run(slot_drop_async_main())
    print("slot-drop-async dropped:", view_drops())
    for n in slot_drop_unpack_gen(True):
        print("slot-drop-unpack-gen", n)
    print("slot-drop-unpack-gen dropped:", view_drops())
    slot_drop_unpack_plain()
    print("slot-drop-unpack-plain dropped:", view_drops())
    for n in slot_drop_walrus_gen():
        print("slot-drop-walrus-gen", n)
    print("slot-drop-walrus-gen dropped:", view_drops())
    slot_drop_view_before(4)
    print("slot-drop-view-before dropped:", view_drops())
    slot_drop_view_after(2)
    print("slot-drop-view-after dropped:", view_drops())
    slot_drop_borrow_unpack_main()
    print("slot-drop-borrow-unpack dropped:", view_drops())


main()
