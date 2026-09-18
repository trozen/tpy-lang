# A non-value local bound at two sites in a `for` or `while` body (if/else
# arms, or once in the body and again after the loop) and READ AFTER the loop:
# sema's loop-level hoist takes the pointer flavor (`T* p;` before the loop, one
# `__slot_N` per bind site), the render the if / try / with ladders already
# give a reassigned hoist. Every hoisted name is mutated after the loop and the
# change observed -- through a second alias, or on the caller's own object where
# the binding came from one (`Flat` is @nocopy, so its sections would not even
# compile a copy). A holder bound to the loop local keeps the loop's last object
# across the post-loop rebind, as CPython.
#
# The second half covers where that one declaration has to STAND: before an
# unrelated SIBLING loop that binds no part of the name, and outside an
# enclosing if / with / try / match whose arm holds the binding loop.
#
# `sib_readonly` is the one parity-blind section here: a readonly binding
# forbids the mutation that would make a silent copy visible, so it only
# pins the const-pointer render.
from typing import Iterator, Optional
from tpy import int32, int64, Ptr, ReturnException, error_return, nocopy, readonly
from tplib import Box
import asyncio


@nocopy
class Flat:
    def __init__(self, n: int32) -> None:
        self.n = n


class Pic:
    def __init__(self, n: int32) -> None:
        self.n = n

    def bump(self) -> None:
        self.n += 1


class CM:
    def __init__(self, n: int32) -> None:
        self.n = n

    def __enter__(self) -> int32:
        return self.n

    def __exit__(self, et, ev, tb) -> None:
        pass


class MyErr(Exception, ReturnException):
    pass


def halves(n: int32) -> tuple[int32, int32]:
    return (n // 2, n - n // 2)


def take_ptr(p: Pic) -> Ptr[Pic]:
    return p


# Free function: the record bound in both arms, read and mutated after the loop.
def free_two_arms() -> None:
    for i in range(3):  # tpyc: ok
        if i % 2 == 0:
            f = Flat(i)
        else:
            f = Flat(i + 10)
    f.n += 100
    print("free_two_arms", f.n)


# Free function: bound once in the body, read after the loop, then rebound;
# the alias taken in the body keeps the loop's last object.
def free_rebind_after() -> None:
    for i in range(3):  # tpyc: ok
        p = Pic(i)
        saved = p
    print("free_rebind_after", p.n, saved.n)
    p = Pic(9)
    p.n += 1
    saved.n += 1
    print("free_rebind_after", p.n, saved.n)


# Free function: a mutable alias of the body local, read after the loop (the
# pointer local lifts off the optional-storage source).
def free_alias_holder() -> None:
    for i in range(3):  # tpyc: ok
        p = Pic(i)
        alias = p
    alias.n += 5
    print("free_alias_holder", alias.n, p.n)


# ... and an alias that is only read, which still observes a mutation of its
# source (a plain pointer local: nothing marks the alias readonly).
def free_alias_const() -> None:
    for i in range(3):  # tpyc: ok
        p = Pic(i)
        view = p
    p.n += 5
    print("free_alias_const", view.n)


# Containers take the same pointer flavor.
def free_list() -> None:
    for i in range(2):  # tpyc: ok
        v = [i]
        v.append(0)
    v.append(1)
    print("free_list", v)
    v = [9]
    print("free_list", v)


def free_dict() -> None:
    for i in range(2):  # tpyc: ok
        if i == 0:
            d = {i: i}
        else:
            d = {i: i * 10}
    d[7] = 7
    print("free_dict", sorted(d.items()))


# Pointer-repr Optional bound in the body (None on one path), read after the
# loop and rebound.
def free_optional() -> None:
    for i in range(2):  # tpyc: ok
        p: Optional[Pic] = Pic(i)
        if i == 1:
            p = None
    print("free_optional", p is None)
    p = Pic(9)
    if p is not None:
        p.n += 1
        print("free_optional", p.n)


# A Box takes the pointer flavor like any @nocopy record.
def free_box() -> None:
    for i in range(2):  # tpyc: ok
        if i == 0:
            b = Box(Pic(i))
        else:
            b = Box(Pic(i + 10))
    print("free_box", b.n)


# Nested loops: the inner loop hoists into the outer body; a name bound in
# the inner loop and read after the OUTER loop hoists at the outer for.
def nested_inner_read() -> None:
    for i in range(2):
        for j in range(2):  # tpyc: ok
            if j == 0:
                f = Flat(i * 10 + j)
            else:
                f = Flat(i * 10 + j + 5)
        f.n += 100
        print("nested_inner_read", f.n)


def nested_outer_read() -> None:
    for i in range(2):  # tpyc: ok
        for j in range(2):
            f = Flat(i * 10 + j)
            saved = f
    f.n += 100
    print("nested_outer_read", f.n, saved.n)
    f = Flat(9)
    print("nested_outer_read", f.n, saved.n)


# ... three deep, with a rebind in the middle body.
def nested_three_deep() -> None:
    for i in range(2):  # tpyc: ok
        for j in range(2):
            for k in range(2):
                f = Flat(i * 100 + j * 10 + k)
            f = Flat(f.n + 1000)
    f.n += 1
    print("nested_three_deep", f.n)


# ... bound in the outer body and rebound in the inner loop.
def nested_outer_bind_inner_rebind() -> None:
    for i in range(2):  # tpyc: ok
        f = Flat(i)
        for j in range(2):
            f = Flat(i * 10 + j)
        print("nested_outer_bind_inner_rebind", f.n)
    f.n += 100
    print("nested_outer_bind_inner_rebind", f.n)


# While loops take the same hoist: two sites in the body, and a body bind
# read after the loop then rebound.
def while_two_arms() -> None:
    i = 0
    while i < 3:  # tpyc: ok
        if i % 2 == 0:
            f = Flat(i)
        else:
            f = Flat(i + 10)
        i += 1
    f.n += 100
    print("while_two_arms", f.n)


def while_rebind_after() -> None:
    i = 0
    while i < 3:  # tpyc: ok
        p = Pic(i)
        saved = p
        i += 1
    print("while_rebind_after", p.n, saved.n)
    p = Pic(9)
    p.n += 1
    print("while_rebind_after", p.n, saved.n)


# A `while` counts as run when its entry condition is provably true: the
# literal, or an integer comparison the value ranges decide (every `i = 0;
# while i < n` above); an unprovable head keeps the read rejected
# (`pointers/error_uninit_while`).
def while_true_break() -> None:
    n = 0
    while True:  # tpyc: ok
        f = Flat(n)
        n += 1
        if n == 2:
            break
    f.n += 100
    print("while_true_break", f.n)


# ... nested either way round.
def while_in_for() -> None:
    for i in range(2):  # tpyc: ok
        j = 0
        while j < 2:
            f = Flat(i * 10 + j)
            j += 1
    f.n += 100
    print("while_in_for", f.n)


def for_in_while() -> None:
    i = 0
    while i < 2:  # tpyc: ok
        for j in range(2):
            f = Flat(i * 10 + j)
        i += 1
    f.n += 100
    print("for_in_while", f.n)


# A tuple-unpack target bound only in the while body, read after the loop
# (value elements: a str view target dangles into the per-iteration tuple,
# BUGS.md#loop-body-view-unpack-target-dangles, the same for both loops).
def while_unpack() -> None:
    n = 40
    i = 0
    while i < 2:  # tpyc: ok
        n, kept = halves(n)
        i += 1
    print("while_unpack", kept, n)


# A walrus inside a comprehension binds in the enclosing function (PEP 572);
# the comprehension's own variable does not leak, the target is read after
# the loop that holds the comprehension.
def comp_walrus_in_loop() -> None:
    for i in range(2):  # tpyc: ok
        ys = [(y := x * 10) for x in range(i + 1)]
    print("comp_walrus_in_loop", y, ys)


class Holder:
    # Method position.
    def run(self) -> None:
        for i in range(2):  # tpyc: ok
            if i == 0:
                p = Flat(i)
            else:
                p = Flat(-i)
            saved = p
        p.n += 100
        print("method", p.n, saved.n)
        p = Flat(9)
        print("method", p.n, saved.n)


class Built:
    v: int32

    # Constructor position.
    def __init__(self) -> None:
        for i in range(2):  # tpyc: ok
            if i == 0:
                p = Flat(i)
            else:
                p = Flat(i + 20)
        p.n += 1
        self.v = p.n


# Generator: the loop local lives on the frame, with the same per-site slots
# as frame fields.
def gen_section() -> Iterator[int32]:
    for i in range(2):  # tpyc: ok
        p = Pic(i)
        yield p.n
    p.n += 10
    yield p.n
    p = Pic(9)
    yield p.n


def gen_while_section() -> Iterator[int32]:
    i = 0
    while i < 2:  # tpyc: ok
        p = Pic(i)
        yield p.n
        i += 1
    p.n += 10
    yield p.n
    p = Pic(9)
    yield p.n


# ... and bound at ONE site with no rebind after the loop: the promoted name
# must still be a frame field, since the loop suspends and the read after
# it runs in another state (record, scalar and unpack target, both loops).
def gen_for_single() -> Iterator[int32]:
    for i in range(2):  # tpyc: ok
        f = Flat(i)
        x = i * 10
        yield f.n + x
    f.n += 100
    x += 1
    yield f.n + x


# ... and a str local sliced out of another local in a generator's loop body:
# the source is a frame FIELD, so it outlives every suspension and the slice
# keeps the view a free function gets (`std::string_view c`, not a copy per
# character). The source is long enough that a dangling view would show.
def gen_slice_view(line: str) -> Iterator[int32]:
    i = 0
    while i < len(line):
        c = line[i:i + 2]  # tpyc: ok
        yield int32(len(c))
        i += 2


def pair(i: int32) -> tuple[int32, str]:
    return (i, "v")


def gen_while_single() -> Iterator[str]:
    i = 0
    while i < 2:  # tpyc: ok
        f = Flat(i)
        a, b = pair(i)
        yield b
        i += 1
    f.n += 100
    yield f"{b}{a}{f.n}"


# Closure body: the free-function path.
def nested_def_section() -> None:
    def inner() -> None:
        for i in range(2):  # tpyc: ok
            if i == 0:
                f = Flat(i)
            else:
                f = Flat(i + 30)
        f.n += 1
        print("nested_def", f.n)

    inner()


# ... a nested `def` INSIDE the binding loop: the enclosing-statement stack
# the anchor is taken from has to come back from the nested-def state save
# holding the SAME statement objects, or the read anchors against a clone
# and codegen never sees the pre-declaration.
# The record binding comes from the caller's list, so the post-loop mutation
# is observed on `pics` itself -- a frame-slot copy would leave it at 0.
def nested_def_in_loop(pics: list[Pic]) -> None:
    for i in range(3):
        def double(k: int32) -> int32:
            return k * 2

        x = double(i)  # tpyc: ok
        xs = [x]
        held = pics[i % 2]
    xs.append(9)
    held.n += 100
    print("nested_def_in_loop", x, xs, held.n, pics[0].n)


# ... and a nested `def` between the binding loop and the read.
def nested_def_after_loop() -> None:
    for i in range(3):
        f = Flat(i)  # tpyc: ok

    def bump(k: int32) -> int32:
        return k + 1

    f.n = bump(f.n)
    print("nested_def_after_loop", f.n)


# Context-manager body: the with hoists the name first; the loop's binds are
# reseats of that pointer local.
def with_section() -> None:
    with CM(4) as k:
        for i in range(2):  # tpyc: ok
            if i == 0:
                f = Flat(i)
            else:
                f = Flat(i + k)
        f.n += 1
        print("with_body", f.n)


# try/finally body.
def try_section() -> None:
    try:
        for i in range(2):  # tpyc: ok
            if i == 0:
                f = Flat(i)
            else:
                f = Flat(i + 40)
        f.n += 1
        print("try_finally", f.n)
    finally:
        print("try_finally done")


# @error_return body.
@error_return(MyErr)
def er_section(k: int32) -> int32:
    for i in range(2):  # tpyc: ok
        if i == 0:
            f = Flat(i)
        else:
            f = Flat(i + k)
    f.n += 1
    return f.n


# match arm.
def match_section(n: int32) -> None:
    match n:
        case 1:
            for i in range(2):  # tpyc: ok
                if i == 0:
                    f = Flat(i)
                else:
                    f = Flat(i + 60)
            f.n += 1
            print("match_arm", f.n)
        case _:
            print("match_arm other")


# --- Sibling loop: an unrelated loop stands between the binding loop and the
# read, so the one declaration has to go before the FIRST of them.

# Free function, scalar: the second loop binds nothing of the name.
def sib_scalar() -> None:
    for i in range(2):
        v = i + 1  # tpyc: ok
    for k in range(2):
        print("sib_scalar", k)
    v = v + 1
    print("sib_scalar", v)


# Free function, @nocopy record mutated through the hoisted name after the
# sibling loop; the alias taken in the body observes the mutation.
def sib_record() -> None:
    for i in range(2):
        f = Flat(i)  # tpyc: ok
        saved = f
    for k in range(2):
        print("sib_record", k)
    f.n += 100
    print("sib_record", f.n, saved.n)


# Free function, Ptr[T] taken in the body: the pointer local must not be the
# uninitialized second declaration (that one panics on the deref).
def sib_ptr() -> None:
    pics = [Pic(100), Pic(101)]
    for p in pics:
        q = take_ptr(p)  # tpyc: ok
    for k in range(2):
        print("sib_ptr", k)
    q.bump()
    print("sib_ptr", q.n, pics[1].n)


# Free function, a tuple-unpack target and the tuple itself (value elements:
# a str view target would dangle into the per-iteration tuple either way,
# BUGS.md#loop-body-view-unpack-target-dangles).
def sib_unpack() -> None:
    n = 40
    for i in range(2):
        n, kept = halves(n)  # tpyc: ok
        t = (n, kept)
    for k in range(2):
        print("sib_unpack", k)
    print("sib_unpack", kept, n, t)


# Free function, Box[T] (@nocopy, so a silent copy would not compile).
def sib_box() -> None:
    for i in range(2):
        b = Box(Pic(i))  # tpyc: ok
    for k in range(2):
        print("sib_box", k)
    print("sib_box", b.n)


# Free function, pointer-repr Optional and value-repr Optional. The pointer
# one binds a PRE-EXISTING element, so the post-loop mutation through it is
# observable on the source -- a copy would leave `base[1]` at 1.
def sib_optional() -> None:
    base = [Pic(0), Pic(1)]
    for i in range(2):
        p: Optional[Pic] = base[i]  # tpyc: ok
        m: Optional[int32] = i * 10
    for k in range(2):
        print("sib_optional", k)
    if p is not None:
        p.n += 1
        print("sib_optional", p.n, m, base[1].n)


# A UNION-typed local would go here; every position rejects it at lowering
# (BUGS.md#union-local-hoist-unclassified), so there is nothing to pin yet.


# Free function, a readonly alias in both arms: the const-pointer flavor.
# Parity-blind by construction -- see the top-of-file comment.
def sib_readonly(a: readonly[Pic], b: readonly[Pic]) -> None:
    for i in range(2):
        if i == 0:
            v = a  # tpyc: ok
        else:
            v = b
    for k in range(2):
        print("sib_readonly", k)
    print("sib_readonly", v.n)


# Free function, a container element borrowed in the body and grown after the
# sibling loop -- the growth lands in the container, not in a copy.
def sib_container_elem() -> None:
    xs = [[0], [1]]
    for i in range(2):
        e = xs[i]  # tpyc: ok
    for k in range(2):
        print("sib_container_elem", k)
    e.append(9)
    print("sib_container_elem", xs)


# --- Two loops binding ONE name to a container LITERAL. Two literals are
# distinct pending types even when they denote the same type, so the shared
# entry has to keep the FIRST binding's -- re-recording the later one moves
# the declaration past the first loop and declares the name twice. The alias
# taken in the first body shows which list the read reached.

# Flat sibling loops; the second runs zero times on the first call.
def two_loop_list_sibling(n: int32) -> None:
    for j in range(2):
        xs = [j]  # tpyc: ok
        held = xs
    for i in range(n):
        xs = [i, i]
    xs.append(9)
    print("two_loop_list_sibling", xs, held)


# ... the same two-loop shape over str/bytes, where the ONE declaration also
# has to agree on the STORAGE: a view slot holding the second loop's owned
# concatenation would dangle past it, so the join takes the strictest binding
# and the slot owns whenever any binding does. `two_loop_str_views` renders
# `std::string t` -- an owning slot copying each slice -- although BOTH its
# bindings slice the live parameter: a slice is one of the shapes the rebind
# check fails closed on, BUGS.md#view-storage-join-stricter-after-first-binding.
# The values are long enough that a dangling read would not survive in an SSO
# buffer.
def two_loop_str_views(s: str, n: int32) -> None:
    for j in range(2):
        t = s[j:]  # tpyc: ok
    for i in range(n):
        t = s[i + 1:]
    print("two_loop_str_views", t)


def two_loop_str_owned(s: str, n: int32) -> None:
    for j in range(2):
        t = "a literal long enough to outrun any small-string buffer"  # tpyc: ok
    for i in range(n):
        t = s + str(i)
    print("two_loop_str_owned", t)


def two_loop_bytes_owned(b: bytes, n: int32) -> None:
    for j in range(2):
        v = b"a literal long enough to outrun any small buffer"  # tpyc: ok
    for i in range(n):
        v = b + b"0123456789012345678901234567890123456789"
    print("two_loop_bytes_owned", v)


# ... and the MIXED pair: the first loop's binding is a slice (a view of the
# live parameter), the second's is a concatenation that owns. One local, one
# storage -- the join owns, so the slice binding copies into it rather than
# leaving a view over the concatenation's dead temporary.
def two_loop_str_view_then_owned(s: str, n: int32) -> None:
    for i in range(2):
        t = s[i:]  # tpyc: ok
    for j in range(n):
        t = s + str(j)
    print("two_loop_str_view_then_owned", t)


def two_loop_bytes_view_then_owned(b: bytes, n: int32) -> None:
    for i in range(2):
        v = b[i:]  # tpyc: ok
    for j in range(n):
        v = b + b"0123456789012345678901234567890123456789"
    print("two_loop_bytes_view_then_owned", v)


# ... and the same join with no loop, at the two faces a rebind into an
# already-owning bytes slot can take: a slice EXPRESSION and a view LOCAL.
# Both need the family's view->owned construction -- `::tpy::Bytes` has no
# `operator=` from a span, so a bare assign would not compile.
def flat_bytes_owned_from_view(b: bytes) -> None:
    t = b + b"0123456789012345678901234567890123456789"
    t = b[0:2]  # tpyc: ok
    v = b + b"0123456789012345678901234567890123456789"
    w = b[1:]
    v = w  # tpyc: ok
    print("flat_bytes_owned_from_view", t, v)


# ... the int-literal join: the second loop's value does not fit the first
# binding's default width, so the one local is the wider type.
def two_loop_int_widths(n: int32) -> None:
    for i in range(2):
        x = 1  # tpyc: ok
    for j in range(n):
        # tpyc: warning(/outside default int32 range/)
        x = 1099511627776
    print("two_loop_int_widths", x)


# ... the same over two spelled widths.
def two_loop_int_widen(n: int32) -> None:
    for i in range(2):
        x = int32(1)  # tpyc: ok
    for j in range(n):
        x = int64(1099511627776)
    print("two_loop_int_widen", x)


# ... an EMPTY list literal in the first loop: the element type comes from the
# binding that has one. The alias taken in the second body shows the append
# after the loops lands in the one list, not in a copy.
def two_loop_empty_list(n: int32) -> None:
    for i in range(2):
        xs = []  # tpyc: ok
    # Narrowed: `held` is bound only by the second loop, so that loop has to
    # provably run for the read after it.
    if n < 1:
        return
    for j in range(n):
        xs = [1, 2]
        held = xs
    xs.append(9)
    print("two_loop_empty_list", xs, held)


# ... the same join with no loop at all: position does not change the answer.
def flat_empty_list() -> None:
    xs = []  # tpyc: ok
    xs = [1, 2]
    xs.append(9)
    print("flat_empty_list", xs)


# ... and with the empty binding in an `if` arm, the second at function level.
def arm_empty_list(flag: bool) -> None:
    if flag:
        xs = []  # tpyc: ok
    xs = [1, 2]
    xs.append(9)
    print("arm_empty_list", xs)


# ... `None` then a record: the join is the Optional. The alias taken in the
# body is mutated after the loops and read through the other name.
def two_loop_none_then_record(n: int32) -> None:
    for i in range(2):
        p = None  # tpyc: ok
    # Narrowed: `saved` is bound only by the second loop, so that loop has to
    # provably run for the read after it.
    if n < 1:
        print("two_loop_none_then_record none")
        return
    for j in range(n):
        p = Pic(j)
        saved = p
    if p is not None:
        saved.n += 100
        print("two_loop_none_then_record", p.n, saved.n)
    else:
        print("two_loop_none_then_record none")


# ... the second loop nested in an `if` arm, over a dict.
def two_loop_dict_arm(flag: bool) -> None:
    for j in range(2):
        d = {j: j}  # tpyc: ok
        held = d
    if flag:
        for i in range(2):
            d = {i: i * 10}
    d[7] = 7
    print("two_loop_dict_arm", sorted(d.items()), sorted(held.items()))


# --- Resumable positions at the two anchor shapes: the promoted name is a
# frame field either way.

# Generator, sibling loop between the binding loop and the read. The name binds
# the CALLER's element, so the post-loop mutation is observed on `pics` -- a
# frame-slot copy would leave it behind.
def gen_sibling(pics: list[Pic]) -> Iterator[int32]:
    for i in range(2):
        p = pics[i]  # tpyc: ok
        yield p.n
    for k in range(2):
        yield k + 50
    p.n += 100
    yield p.n


# Generator, binding loop in an `if` arm.
def gen_blk_if(flag: bool, pics: list[Pic]) -> Iterator[int32]:
    if flag:
        for i in range(2):
            p = pics[i]  # tpyc: ok
            yield p.n
    else:
        for j in range(2):
            p = pics[j]
            yield p.n + 10
    p.n += 100
    yield p.n


# async, sibling loop.
async def async_sibling(pics: list[Pic]) -> int32:
    for i in range(2):
        p = pics[i]  # tpyc: ok
        await asyncio.sleep(0)
    for k in range(2):
        await asyncio.sleep(0)
    p.n += 100
    return p.n


# async, binding loop in an `if` arm.
async def async_blk_if(flag: bool, pics: list[Pic]) -> int32:
    if flag:
        for i in range(2):
            p = pics[i]  # tpyc: ok
            await asyncio.sleep(0)
    else:
        for j in range(2):
            p = pics[j]
            await asyncio.sleep(0)
    p.n += 100
    return p.n


async def async_all() -> None:
    pics = [Pic(0), Pic(1)]
    print("async_sibling", await async_sibling(pics), pics[1].n)
    pics2 = [Pic(0), Pic(1)]
    print("async_blk_if", await async_blk_if(True, pics2), pics2[1].n)
    pics3 = [Pic(0), Pic(1)]
    print("async_blk_if", await async_blk_if(False, pics3), pics3[1].n)


# --- The binding loop nested in a non-loop block, with the read AFTER it.

# `for` in both arms of an `if`.
def blk_if(flag: bool) -> None:
    if flag:
        for i in range(2):
            f = Flat(i)  # tpyc: ok
    else:
        for j in range(2):
            f = Flat(j + 70)
    f.n += 1
    print("blk_if", f.n)


# ... with the other arm assigning the name directly: the arm's binding is
# block-local, so the loop's pending one is what the read declares.
def blk_if_mixed(flag: bool) -> None:
    if flag:
        for i in range(2):
            f = Flat(i)  # tpyc: ok
    else:
        f = Flat(99)
    f.n += 1
    print("blk_if_mixed", f.n)


# ... the mirror, with the direct assignment in the FIRST arm: the cross-arm
# unbind has to be symmetric.
def blk_if_mixed_rev(flag: bool) -> None:
    if flag:
        f = Flat(99)
    else:
        for j in range(2):
            f = Flat(j + 70)  # tpyc: ok
    f.n += 1
    print("blk_if_mixed_rev", f.n)


# ... and a loop BEFORE the `if` making the name pending, with one arm then
# assigning it directly: the pending entry stays the authority, so the arm's
# binding is the one taken back out of the scope.
def blk_pre_loop_arm(flag: bool) -> None:
    for i in range(2):
        f = Flat(i)  # tpyc: ok
    if flag:
        f = Flat(99)
    f.n += 1
    print("blk_pre_loop_arm", f.n)


# ... the binding loop BEFORE the `if`, with the arm rebinding the name from
# an owning source: the one local owns, so the loop's slice binding copies.
def blk_loop_then_arm(s: str, flag: bool) -> None:
    for i in range(2):
        t = s[i:]  # tpyc: ok
    if flag:
        t = s + "tail"
    print("blk_loop_then_arm", t)


# ... and READ in both arms of a later `if`: the first arm's read promotes the
# one declaration, which is not the arm's to keep -- the second arm has to
# reach the same promotion rather than a local it cannot prove assigned.
def blk_read_in_both_arms(flag: bool) -> None:
    for i in range(2):
        p = Pic(i)  # tpyc: ok
    if flag:
        p.n += 1
    else:
        p.n += 2
    print("blk_read_in_both_arms", p.n)


# ... and the sibling of that shape where each arm has its OWN binding loop and
# reads the name inside the arm: the then-arm's promotion is no more the arm's
# to keep than a promotion of a name pending before the `if`, or the else-arm's
# loop rebinds a name already in scope and its read cannot prove it assigned.
def blk_loop_in_both_arms(flag: bool) -> None:
    if flag:
        for i in range(2):
            f = Flat(i)  # tpyc: ok
        f.n += 1
    else:
        for k in range(3):
            f = Flat(k + 10)
        f.n += 2
    f.n += 100
    print("blk_loop_in_both_arms", f.n)


# ... and a `while` in the arm, which the direct spelling already accepted.
def blk_if_while(flag: bool) -> None:
    if flag:
        n = 0
        while n < 2:
            w = n  # tpyc: ok
            n += 1
    else:
        w = 9
    print("blk_if_while", w)


# Context-manager body, read after the `with`.
def blk_with() -> None:
    with CM(4) as k:
        for i in range(2):
            f = Flat(i + k)  # tpyc: ok
    f.n += 1
    print("blk_with", f.n)


# try body and handler, read after the try.
def blk_try(n: int32) -> None:
    try:
        for i in range(2):
            f = Flat(i + n)  # tpyc: ok
    except ValueError:
        for j in range(2):
            f = Flat(j + 50)
    f.n += 1
    print("blk_try", f.n)


# match arms, read after the match. Value local: a non-value hoist at a
# scalar-switch match is not an admitted flavor on that ladder
# (BUGS.md#match-nonvalue-hoist-unadmitted).
def blk_match(n: int32) -> None:
    match n:
        case 1:
            for i in range(2):
                v = i + 1  # tpyc: ok
        case _:
            for j in range(2):
                v = j + 80
    v += 1
    print("blk_match", v)


class SibHolder:
    # Method position, sibling loop.
    def run(self) -> None:
        for i in range(2):
            f = Flat(i)  # tpyc: ok
        for k in range(2):
            print("sib_method", k)
        f.n += 100
        print("sib_method", f.n)


class SibBuilt:
    v: int32

    # Constructor position, sibling loop.
    def __init__(self) -> None:
        for i in range(2):
            f = Flat(i + 20)  # tpyc: ok
        for k in range(2):
            pass
        f.n += 1
        self.v = f.n


# Closure position, sibling loop.
def sib_closure() -> None:
    def inner() -> None:
        for i in range(2):
            f = Flat(i + 30)  # tpyc: ok
        for k in range(2):
            pass
        f.n += 1
        print("sib_closure", f.n)

    inner()


# @error_return body, sibling loop.
@error_return(MyErr)
def sib_er(k: int32) -> int32:
    for i in range(2):
        f = Flat(i + k)  # tpyc: ok
    for j in range(2):
        pass
    f.n += 1
    return f.n


def main() -> None:
    free_two_arms()
    free_rebind_after()
    free_alias_holder()
    free_alias_const()
    free_list()
    free_dict()
    free_optional()
    free_box()
    nested_inner_read()
    nested_outer_read()
    nested_three_deep()
    nested_outer_bind_inner_rebind()
    while_two_arms()
    while_rebind_after()
    while_true_break()
    while_in_for()
    for_in_while()
    while_unpack()
    comp_walrus_in_loop()
    Holder().run()
    print("ctor", Built().v)
    for v in gen_section():
        print("gen", v)
    for v in gen_while_section():
        print("gen_while", v)
    for v in gen_for_single():
        print("gen_for_single", v)
    for t in gen_while_single():
        print("gen_while_single", t)
    slice_total = 0
    for v in gen_slice_view("a source long enough that a dangling view shows"):
        slice_total += v
    print("gen_slice_view", slice_total)
    nested_def_section()
    nested_def_in_loop([Pic(0), Pic(1)])
    nested_def_after_loop()
    with_section()
    try_section()
    try:
        print("error_return", er_section(50))
    except MyErr:
        print("error_return raised")
    match_section(1)
    match_section(2)
    sib_scalar()
    sib_record()
    sib_ptr()
    sib_unpack()
    sib_box()
    sib_optional()
    sib_readonly(Pic(1), Pic(2))
    sib_container_elem()
    two_loop_list_sibling(0)
    two_loop_list_sibling(2)
    two_loop_dict_arm(False)
    two_loop_dict_arm(True)
    long_s = "a parameter long enough that a dangling view would show, truly"
    two_loop_str_views(long_s, 0)
    two_loop_str_views(long_s, 2)
    two_loop_str_owned(long_s, 0)
    two_loop_str_owned(long_s, 2)
    long_b = b"a parameter long enough that a dangling view would show"
    two_loop_bytes_owned(long_b, 0)
    two_loop_bytes_owned(long_b, 2)
    two_loop_str_view_then_owned(long_s, 0)
    two_loop_str_view_then_owned(long_s, 2)
    two_loop_bytes_view_then_owned(long_b, 0)
    two_loop_bytes_view_then_owned(long_b, 2)
    flat_bytes_owned_from_view(long_b)
    two_loop_int_widths(0)
    two_loop_int_widths(2)
    two_loop_int_widen(0)
    two_loop_int_widen(2)
    two_loop_empty_list(2)
    flat_empty_list()
    arm_empty_list(True)
    arm_empty_list(False)
    two_loop_none_then_record(0)
    two_loop_none_then_record(2)
    gen_pics = [Pic(0), Pic(1)]
    for v in gen_sibling(gen_pics):
        print("gen_sibling", v)
    print("gen_sibling", gen_pics[1].n)
    if_pics = [Pic(0), Pic(1)]
    for v in gen_blk_if(True, if_pics):
        print("gen_blk_if", v)
    print("gen_blk_if", if_pics[1].n)
    if_pics2 = [Pic(0), Pic(1)]
    for v in gen_blk_if(False, if_pics2):
        print("gen_blk_if", v)
    print("gen_blk_if", if_pics2[1].n)
    asyncio.run(async_all())
    blk_if(True)
    blk_if(False)
    blk_if_mixed(True)
    blk_if_mixed(False)
    blk_if_mixed_rev(True)
    blk_if_mixed_rev(False)
    blk_pre_loop_arm(True)
    blk_pre_loop_arm(False)
    blk_loop_then_arm(long_s, True)
    blk_loop_then_arm(long_s, False)
    blk_read_in_both_arms(True)
    blk_read_in_both_arms(False)
    blk_loop_in_both_arms(True)
    blk_loop_in_both_arms(False)
    blk_if_while(True)
    blk_if_while(False)
    blk_with()
    blk_try(3)
    blk_match(1)
    blk_match(2)
    SibHolder().run()
    print("sib_ctor", SibBuilt().v)
    sib_closure()
    try:
        print("sib_error_return", sib_er(50))
    except MyErr:
        print("sib_error_return raised")


main()

# Module level: the same sibling shape at top level, where the promoted name
# is a local of the module initializer.
for mi in range(2):
    mf = Flat(mi)  # tpyc: ok
for mk in range(2):
    pass
mf.n += 100
print("module_sibling", mf.n)
