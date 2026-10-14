# A rebound tuple holding an object of its own is the pointer tuple, each owned
# element the scalar reference local at its site; one section per position.
# Each section mutates through an alias after the boundary it tests. The
# warned sections print only what both runs agree on: return-borrowed and
# own-arg (the warned copy at an owning slot), name-element and swap-names
# (the warned copy into an owned position), borrowed-place and field-place
# (the warned mutation of storage the binding still borrows), walk-rebind
# and copy-carry (a loop rebind's warned slot reuse, run once), and
# loop-two-site, loop-two-site-two-hop and loop-two-site-while (a loop
# rebind's warned clobber of a live alias,
# BUGS.md#loop-rebind-site-slot-reuse-warned, run once). The untracked-*,
# retain-*, tuple-view, range-field, copied-elem and mapped-elem sections bind
# a holder by a path no tracked loan records (two hops in, a view, a capture,
# a callee keeping a pointer): every rebind takes storage of its own, so the
# old object outlives it.
import asyncio
from typing import Callable, Iterator

from tpy import (Own, Ptr, ReturnException, StrView, ValueType, copy,
                 error_return, int32, nocopy, take_ptr)


class Box:
    def __init__(self, n: int32) -> None:
        self.n = n


class Bag:
    xs: list[int32]

    def __init__(self, n: int32) -> None:
        self.xs = [n]


@nocopy
class Tok:
    def __init__(self, n: int32) -> None:
        self.n = n


class Holder:
    toks: tuple[Tok, Tok]

    def __init__(self, b: Box) -> None:
        self.toks = (Tok(40), Tok(b.n))

    def method(self, v: Box) -> None:
        p = make_mixed(1, v)
        a = p[0]
        a2 = p[0]
        # method: 'a' still reads the old element, so the new one takes a slot.
        p = make_mixed(9, v)  # tpyc: ok
        a.n += 100
        print("method", a2.n, p[0].n)


class Built:
    seen: int32

    def __init__(self, v: Box) -> None:
        p = make_mixed(1, v)
        a = p[0]
        a2 = p[0]
        # constructor: 'a' still reads the old element, so the new one takes
        # a slot of its own.
        p = make_mixed(9, v)  # tpyc: ok
        a.n += 100
        self.seen = a2.n * 100 + p[0].n


class Fail(Exception, ReturnException):
    pass


class Ctx:
    def __enter__(self) -> int32:
        return 1

    def __exit__(self, et, ev, tb) -> None:
        pass


def make_mixed(n: int32, b: Box) -> tuple[Own[Box], Box]:
    return (Box(n), b)


def make_bag(n: int32, b: Bag) -> tuple[Own[Bag], Bag]:
    return (Bag(n), b)


def make_str(n: int32, b: Box) -> tuple[Own[Tok], Box, str]:
    return (Tok(n), b, "x" * (1024 + n))


def tok_int(n: int32) -> tuple[Own[Tok], int32]:
    return (Tok(n), n)


def make_tok(n: int32, b: Box) -> tuple[Own[Tok], Box]:
    return (Tok(n), b)


def pair(n: int32) -> Own[tuple[Box, Box]]:
    return (Box(n), Box(n + 1))


def tok_pair(n: int32) -> Own[tuple[Tok, Tok]]:
    return (Tok(n), Tok(n + 1))


def pick(xs: list[Box], b: Box) -> tuple[Box, Box]:
    return (xs[0], b)


def bump(b: Box, k: int32) -> int32:
    b.n += k
    return b.n


def fail() -> int32:
    raise ValueError("boom")


def sink(p: tuple[Own[Tok], Box, str]) -> Own[Tok]:
    p[1].n += 1
    a, b, s = p
    a.n += len(s)
    return a


def bag_pair(n: int32) -> Own[tuple[Bag, Bag]]:
    return (Bag(n), Bag(n + 1))


def bag_sink(p: Own[tuple[Bag, Bag]]) -> int32:
    return len(p[0].xs) + len(p[1].xs)


def own_sink(p: Own[tuple[Tok, Box]]) -> int32:
    return p[0].n * 100 + p[1].n


def retain_early(v: Box) -> None:
    p = make_mixed(1, v)
    # retain-early: a call defined below keeps nothing of 'p' through an
    # unrelated operand -- written in place, as in retain-late.
    p = defined_later(9, v)  # tpyc: ok
    p[1].n += 1
    # ... and may keep the old element handed to it: a slot of its own.
    p = defined_later(7, p[0])  # tpyc: ok
    p[1].n += 100
    print("retain-early", v.n, p[0].n, p[1].n)


def defined_later(n: int32, b: Box) -> tuple[Own[Box], Box]:
    return (Box(n), b)


def retain_late(v: Box) -> None:
    p = make_mixed(1, v)
    # retain-late: the same calls after the callee's definition.
    p = defined_later(9, v)  # tpyc: ok
    p[1].n += 1
    p = defined_later(7, p[0])  # tpyc: ok
    p[1].n += 100
    print("retain-late", v.n, p[0].n, p[1].n)


def function(v: Box, w: Box) -> None:
    p = make_mixed(1, v)
    a = p[0]
    a2 = p[0]
    # function: 'a' still reads the old element, so the new one takes a slot.
    p = make_mixed(9, w)  # tpyc: ok
    a.n += 100
    p[1].n += 1
    print("function", a2.n, p[0].n, w.n)


def in_place_then_own(v: Box) -> None:
    p = make_mixed(1, v)
    # in-place-then-own: nothing reads the old element yet -- written in place.
    p = make_mixed(2, v)  # tpyc: ok
    a = p[0]
    a2 = p[0]
    # ... and here 'a' does, so the element takes a slot of its own.
    p = make_mixed(3, v)  # tpyc: ok
    a.n += 100
    print("in-place-then-own", a2.n, p[0].n)


def rhs_retains(b: Box) -> None:
    p = make_mixed(1, b)
    a = p[0]
    # rhs-retains: the new value borrows the old element, which must survive.
    p = make_mixed(9, p[0])  # tpyc: ok
    p[1].n += 100
    print("rhs-retains", p[0].n, a.n)


def eval_order() -> None:
    p = (Box(1), 0)
    # eval-order: the old element is read before anything is rebound.
    p = (Box(9), p[0].n)  # tpyc: ok
    print("eval-order", p[0].n, p[1])
    try:
        # ... and a raising element leaves the tuple as it was.
        p = (Box(5), fail())  # tpyc: ok
    except ValueError:
        p[0].n += 1
        print("eval-order-raise", p[0].n, p[1])


def hoisted(c: bool) -> None:
    if c:
        t = pair(1)
    else:
        t = pair(2)
    u = t
    u2 = t
    # hoisted: the whole aliases keep both of the old elements.
    t = pair(9)  # tpyc: ok
    u[0].n += 100
    t[1].n += 1
    print("hoisted", u2[0].n, u2[1].n, t[0].n, t[1].n)


def deleted(b: Box) -> None:
    p = make_mixed(1, b)
    a = p[0]
    a2 = p[0]
    del p
    # del: the deleted tuple's element is still read through 'a'.
    p = make_mixed(9, b)  # tpyc: ok
    a.n += 1
    print("del", a2.n, p[0].n)


def literal(b: Box) -> None:
    t = (Box(1), b)
    # literal: a fresh element written in place, the borrowed one re-pointed.
    t = (Box(2), b)  # tpyc: ok
    t[1].n += 1
    print("literal", t[0].n, t[1].n, b.n)


def owned_call() -> None:
    t = pair(1)
    a = t[1]
    a2 = t[1]
    # owned-call: element 0 in place, element 1 a slot of its own for 'a'.
    t = pair(5)  # tpyc: ok
    a.n += 100
    print("owned-call", a2.n, t[0].n, t[1].n)


def alias_after(v: Box) -> None:
    p = make_mixed(1, v)
    p = make_mixed(2, v)
    # alias-after: a whole alias taken after the rebind is that same tuple.
    t = p  # tpyc: ok
    t[0].n = 7
    print("alias-after", p[0].n, t[0].n)


def inline_then_borrow(xs: list[Box], b: Box) -> None:
    a = Box(1)
    # inline-then-borrow: 'a' moves into the literal at its last use, so the
    # tuple owns element 0 ...
    p = (a, b)
    o = p[0]
    o2 = p[0]
    # ... and a borrowing call's result only re-points the element pointers,
    # so a write through 'p' lands in the list's object.
    p = pick(xs, b)  # tpyc: ok
    o.n += 100
    p[0].n += 1
    print("inline-then-borrow", o2.n, p[0].n, p[1].n, xs[0].n)


def subscript_source(xs: list[tuple[Box, Box]], b: Box) -> None:
    p = make_mixed(1, b)
    a = p[0]
    a2 = p[0]
    # subscript-source: a rebind to a list element re-points both element
    # pointers -- nothing is copied or warned -- and 'a' keeps the old one.
    p = xs[0]  # tpyc: ok
    p[0].n = 55
    a.n += 100
    print("subscript-source", xs[0][0].n, a2.n)


def arg_last_use(c: bool, b: Box) -> None:
    p = make_str(1, b)
    if c:
        p = make_str(2, b)
    # arg-last-use: the uncopyable element and the string move into the
    # argument; the borrowed element stays the caller's object.
    print("arg-last-use", sink(p).n, b.n)  # tpyc: ok


def return_last_use(c: bool) -> Own[tuple[Tok, Tok]]:
    t = tok_pair(1)
    if c:
        t = tok_pair(5)
    # return-last-use: each uncopyable element moves out of its slot.
    return t  # tpyc: ok


def return_borrowed(b: Bag) -> Own[tuple[Bag, Bag]]:
    p = make_bag(1, b)
    p = make_bag(2, b)
    # return-borrowed: the owned element moves out; the borrowed one is the
    # caller's object, copied in (warned), never moved out of the caller.
    return p  # tpyc: warning(/copies Bag into owned storage \(tuple element 1\)/)


def own_arg(b: Box) -> None:
    p = make_tok(1, b)
    p = make_tok(2, b)
    # own-arg: the uncopyable owned element moves into the owning argument,
    # the borrowed one is copied in (warned).
    print("own-arg", own_sink(p), b.n)  # tpyc: warning(/copies Box into owned storage \(tuple element 1\)/)


def consuming(b: Box) -> None:
    p = make_tok(1, b)
    p = make_tok(9, b)
    # consuming: the uncopyable element moves out of its slot into 'a'.
    a, z = p  # tpyc: ok
    a.n += 100
    z.n += 1
    print("consuming", a.n, z.n, b.n)


def try_finally(v: Box) -> None:
    p = make_mixed(1, v)
    a = p[0]
    a2 = p[0]
    try:
        # try-finally: the rebind inside a try body takes a slot for 'a'.
        p = make_mixed(9, v)  # tpyc: ok
    finally:
        a.n += 100
    print("try-finally", a2.n, p[0].n)


def with_body(v: Box) -> None:
    p = make_mixed(1, v)
    a = p[0]
    a2 = p[0]
    with Ctx():
        # with: the rebind inside a with body takes a slot for 'a'.
        p = make_mixed(9, v)  # tpyc: ok
    a.n += 100
    print("with", a2.n, p[0].n)


def mutable_sources(h: Holder) -> None:
    t = tok_pair(1)
    a = t[1]
    t = tok_pair(5)
    t = h.toks
    # mutable-sources: a field source the body writes through, so every
    # source is mutable and the element pointers are mutable too.
    t[0].n += 10  # tpyc: ok
    t = tok_pair(7)
    print("mutable-sources", a.n, t[0].n, h.toks[0].n)


def name_element(v: Box, w: Box) -> None:
    p = make_mixed(1, v)
    # name-element: a name at a position the binding owns is copied in, the
    # warned copy an `Own` slot takes where CPython shares 'w' (the element
    # is no name of its own yet: TODO.md "A tuple element is a name"); only
    # the tuple's side is printed, which both agree on.
    p = (w, v)  # tpyc: warning(/copies Box into owned storage \(tuple element 0\)/)
    p[0].n += 100
    print("name-element", p[0].n)

def elem_arg(b: Box) -> None:
    t = (b, b)
    # elem-arg: an element of a tuple of names passed where its object is
    # wanted is that object.
    p = make_mixed(9, t[0])  # tpyc: ok
    p[1].n += 100
    print("elem-arg", b.n, p[1].n)


def take_pointer(b: Box) -> None:
    p = make_mixed(1, b)
    a = p[0]
    # take-ptr: a pointer to the owned element's object, kept across the
    # rebind, which gives the new element a slot of its own.
    pt = take_ptr(p[0])  # tpyc: ok
    p = make_mixed(9, b)
    pt.n += 100
    print("take-ptr", a.n, p[0].n)


def elem_reseat(v: Box, w: Box) -> None:
    p = make_mixed(1, v)
    p = make_mixed(2, v)
    z = v
    # elem-reseat: a pointer local re-pointed at an element the tuple holds
    # as a pointer copies the pointer.
    z = p[0]  # tpyc: ok
    z.n += 100
    t = (v, w)
    y = v
    y = t[1]
    y.n += 1
    print("elem-reseat", p[0].n, w.n)


def for_hoist(v: Box) -> None:
    for i in range(2):
        p = make_mixed(i, v)
        if i == 1:
            # for-hoist: bound and rebound in a for body, read after it.
            p = make_mixed(10 + i, v)  # tpyc: ok
    a = p[0]
    a.n += 100
    print("for-hoist", p[0].n)


def name_bound(v: Box, c: bool) -> None:
    p = make_mixed(1, v)
    p = make_mixed(2, v)
    # name-bound: bindings only from the pointer tuple 'p' alias it.
    keep = p
    if c:
        keep = p  # tpyc: ok
    keep[0].n += 5
    u = p
    u = make_mixed(9, v)  # tpyc: ok
    u[0].n += 100
    print("name-bound", p[0].n, keep[0].n, u[0].n)


def own_elem_return(c: bool) -> tuple[Own[Tok], int32]:
    p = tok_int(3)
    if c:
        p = tok_int(4)
    # own-elem-return: the uncopyable element owned on every path moves out.
    return p  # tpyc: ok


def matched(k: int32, v: Box) -> None:
    p = make_mixed(1, v)
    a = p[0]
    a2 = p[0]
    match k:
        case 1:
            # match: the rebind in an arm takes a slot for 'a'.
            p = make_mixed(9, v)  # tpyc: ok
        case _:
            pass
    a.n += 100
    print("match", a2.n, p[0].n)


@error_return(Fail)
def fallible_rebind(v: Box) -> int32:
    p = make_mixed(1, v)
    a = p[0]
    a2 = p[0]
    # error-return: the rebind in an @error_return body takes a slot.
    p = make_mixed(9, v)  # tpyc: ok
    a.n += 100
    return a2.n * 100 + p[0].n


def branch_once(c: bool, v: Box) -> None:
    if c:
        # branch-once: bound once, in a branch, and never rebound -- not the
        # pointer tuple: the mixed call's own render, its object inline.
        t = make_mixed(1, v)  # tpyc: ok
    else:
        return
    t[1].n += 1
    print("branch-once", t[0].n, t[1].n, v.n)


def unpack_alias(b: Box) -> None:
    p = make_mixed(1, b)
    a0, a1 = p
    o = p[0]
    # unpack-alias: the unpack target and 'o' alias the old element.
    p = make_mixed(9, b)  # tpyc: ok
    a0.n += 100
    print("unpack-alias", o.n, a1.n, p[0].n)


class HB:
    bp: tuple[Box, Box]
    b: Box

    def __init__(self) -> None:
        self.bp = (Box(50), Box(60))
        self.b = Box(70)


def closure(v: Box) -> None:
    p = make_mixed(1, v)
    a = p[0]
    p = make_mixed(2, v)

    # closure: a nested def that does not escape reads the rebound tuple.
    def get() -> int32:
        return p[0].n  # tpyc: ok

    p[0].n += 10
    a.n += 100
    print("closure", get(), a.n)


def comprehension(v: Box) -> None:
    p = make_mixed(1, v)
    a = p[0]
    p = make_mixed(2, v)
    a.n += 100
    # comprehension: its body writes through the rebound tuple's element.
    xs = [bump(p[0], i) for i in range(2)]  # tpyc: ok
    p[0].n += 1
    print("comprehension", xs[1], a.n, p[0].n)


def nonlocal_write(v: Box) -> None:
    p = make_mixed(1, v)
    p = make_mixed(2, v)

    # nonlocal: a nested def writing through the enclosing pointer tuple.
    def bump() -> None:
        nonlocal p
        p[0].n += 5  # tpyc: ok

    bump()
    bump()
    print("nonlocal", p[0].n, v.n)


async def frame_body(v: Box) -> int32:
    p = make_mixed(1, v)
    # async: a coroutine's rebound tuple is the pointer tuple too; with no
    # holder the rebind writes in place into the first binding's frame field,
    # and an element alias taken across a suspension writes into that field.
    p = make_mixed(2, v)  # tpyc: ok
    await asyncio.sleep(0)
    a = p[0]
    a.n += 10
    p[1].n += 1
    return p[0].n * 100 + p[1].n


def field_repoint(h: HB) -> None:
    t = pair(1)
    print("field-repoint", t[0].n)
    # field-repoint: a whole field re-points both element pointers; the
    # binding takes the field's address, so 'h' stays a mutable reference.
    t = h.bp  # tpyc: ok
    t[0].n += 1
    print("field-repoint", t[0].n, h.bp[0].n)


def written_then_field(h: HB, v: Box) -> None:
    t = make_mixed(1, v)
    t[0].n += 1
    print("written-then-field", t[0].n)
    # written-then-field: the same after a write through the owned element.
    t = h.bp  # tpyc: ok
    h.bp[0].n += 1
    print("written-then-field", t[0].n, h.bp[0].n)


def match_capture(v: Box) -> None:
    p = make_mixed(1, v)
    p = make_mixed(2, v)
    # match-capture: a capture of an element the tuple holds as a pointer
    # binds its object.
    match p[0]:  # tpyc: ok
        case Box() as x:
            p = make_mixed(9, v)
            x.n += 100
            print("match-capture", x.n, p[0].n)


def hoist_alias(v: Box, c: bool) -> None:
    p = make_mixed(1, v)
    p = make_mixed(2, v)
    q = make_mixed(3, v)
    q = make_mixed(4, v)
    # hoist-alias: a name bound from either pointer tuple in the branches
    # holds their pointers.
    if c:
        u = p  # tpyc: ok
    else:
        u = q
    u[0].n += 100
    print("hoist-alias", p[0].n, q[0].n, u[0].n)


# The callee mutates the owned element but neither stores, forwards nor
# returns it, so the object dies with the call -- warned as a parameter that
# is never consumed; the caller's move is the subject (mixed-arg).
def own_mixed(p: tuple[Own[Bag], Bag]) -> int32:  # tpyc: warning(/owned tuple param 'p' is never consumed/)
    p[0].xs.append(9)
    p[1].xs.append(9)
    return len(p[0].xs) * 10 + len(p[1].xs)


def mixed_arg(v: Bag) -> None:
    p = make_bag(1, v)
    p = make_bag(2, v)
    # mixed-arg: at its last use the owned element moves into the mixed
    # slot, the borrowed one keeps pointing at the caller's object.
    print("mixed-arg", own_mixed(p), len(v.xs))  # tpyc: ok
    v.xs.append(1)
    print("mixed-arg", len(v.xs))


class Oops(Exception):
    pass


def except_binder(v: Box) -> None:
    p = make_mixed(1, v)
    # except-binder: TPy declares an `except` target of the same spelling as
    # the handler's own name, so 'p' stays the pointer tuple (CPython binds
    # and then unbinds 'p' itself: BUGS.md#handler-name-not-unbound).
    p = make_mixed(2, v)  # tpyc: ok
    p[1].n += 1
    print("except-binder", p[0].n, v.n)
    try:
        raise Oops()
    except Oops as p:
        print("except-binder caught")


def except_binder_branches(c: bool) -> None:
    # ... and bound in both branches, each owning call backs its element at
    # its own site.
    if c:
        t = tok_int(3)  # tpyc: ok
    else:
        t = tok_int(4)
    t[0].n += 10
    print("except-binder", t[0].n, t[1])
    try:
        raise Oops()
    except Oops as t:
        print("except-binder caught")


def borrowed_place(xs: list[Box], v: Box) -> None:
    p = make_mixed(1, v)
    # borrowed-place: an element read at a position the binding borrows
    # re-points at the list's object.
    p = (Box(9), xs[0])  # tpyc: ok
    p[1].n += 100
    xs[0].n += 1
    print("borrowed-place", p[0].n, p[1].n, xs[0].n)
    # ... a loan the binding records, so growing the list warns.
    xs.append(Box(5))  # tpyc: warning(/Mutation of 'xs' while borrowed/)
    print("borrowed-place", p[0].n, len(xs))


def field_place(h: HB, v: Box) -> None:
    p = make_mixed(1, v)
    # field-place: a field read at a position the binding borrows re-points
    # at the holder's object ...
    p = (Box(9), h.b)  # tpyc: ok
    p[1].n += 100
    print("field-place", p[0].n, p[1].n, h.b.n)
    # ... a loan the binding records, so rebinding the field warns.
    h.b = Box(71)  # tpyc: warning(/Mutation of 'h.b' while borrowed/)
    print("field-place", p[0].n, h.b.n)


def gen_values(b: Box) -> Iterator[int32]:
    yield b.n
    yield b.n


def gen_loop(v: Box) -> None:
    p = make_mixed(1, v)
    for x in gen_values(p[0]):
        # gen-loop: the generator keeps the old element, so the rebind in
        # the body gives the new one a slot of its own (no clobber warning).
        p = make_mixed(9, v)  # tpyc: ok
        p[0].n += 1
        print("gen-loop", x, p[0].n)


def swap_names(v: Box) -> None:
    p = make_mixed(1, v)
    p = make_mixed(2, v)
    x, y = p
    # swap-names: the literal swap rewritten through names of its own; 'y'
    # at the owned position is copied (warned), 'x' at the borrowed one keeps
    # the old owned object.
    p = (y, x)  # tpyc: warning(/copies Box into owned storage \(tuple element 0\)/)
    x.n += 100
    print("swap-names", p[0].n, p[1].n, x.n, v.n)


V = Box(2)
W = Box(3)

# module-loop: a write in a for body takes the site's hoisted static slot;
# 'saved' still reads the first binding's object.
M = make_mixed(1, V)
saved, _ = M
saved2, _ = M
for i in range(2):
    M = make_mixed(10 + i, V)  # tpyc: ok
    M[0].n += 1
saved.n += 100
print("module-loop", saved2.n, M[0].n)

# module-literal: the fresh element written in place in its static slot.
L = (Box(7), W)
L = (Box(8), W)  # tpyc: ok
L[1].n += 1
print("module-literal", L[0].n, L[1].n, W.n)

# module-call: an element alias makes the rebind take a static of its own.
C = pair(20)
c0, c1 = C
c0b, c1b = C
C = pair(30)  # tpyc: ok
c0.n += 100
print("module-call", c0b.n, c1b.n, C[0].n, C[1].n)

# module-call-alias: a function handing out the tuple global is an alias no
# loan records; the rebind gives the new element a static of its own, as every
# module-level rebind does.
P = make_mixed(1, V)


def grab() -> tuple[Box, Box]:
    return P


S = grab()
S2 = grab()
P = make_mixed(9, V)  # tpyc: ok
S[0].n += 100
print("module-call-alias", S2[0].n, P[0].n)

# module-call-alias-scalar: the scalar twin, a function handing out the global
# itself; the old object survives the rebind.
B = Box(10)


def grab_box() -> Box:
    return B


SB = grab_box()
B = Box(9)  # tpyc: ok
SB.n += 100
print("module-call-alias-scalar", SB.n, B.n)


def alias_order_early(v: Box) -> None:
    s = make_tok(3, v)
    s = make_tok(4, v)
    p = make_tok(1, v)
    p = make_tok(2, v)
    q = p
    r = q
    # alias order (early): 's' is bound before the aliases; 'r' re-points.
    r = q  # tpyc: ok
    q = s
    r = q
    r[0].n += 100
    print("alias-order-early", r[0].n, s[0].n, p[0].n)


def alias_order_late(v: Box) -> None:
    p = make_tok(1, v)
    p = make_tok(2, v)
    q = p
    r = q
    # alias order (late): the same rebind with 's' bound after it; the
    # copy check waits for the whole body, so 'r' re-points here too.
    r = q  # tpyc: ok
    s = make_tok(3, v)
    s = make_tok(4, v)
    q = s
    r = q
    r[0].n += 100
    print("alias-order-late", r[0].n, s[0].n, p[0].n)


def alias_flat(v: Box, c: bool) -> None:
    p = make_tok(1, v)
    p = make_tok(2, v)
    q = p
    # alias in the body: 'q' re-points at 'p''s element.
    if c:
        q = p  # tpyc: ok
    q[0].n += 100
    print("alias-flat", q[0].n, p[0].n)


def alias_nested(v: Box, c: bool) -> None:
    p = make_tok(1, v)
    p = make_tok(2, v)

    def k() -> int32:
        q = p
        # alias in a nested def: the same rebind over the enclosing pointer
        # tuple, decided against the enclosing body's final answer.
        if c:
            q = p  # tpyc: ok
        q[0].n += 100
        return q[0].n
    print("alias-nested", k(), p[0].n)


def relay(it: Iterator[int32]) -> Iterator[int32]:
    for x in it:
        yield x


def genexpr_read(v: Box) -> None:
    p = make_mixed(1, v)
    p = make_mixed(2, v)
    a = p[0]
    # genexpr: the frame's capture of 'p' reads the pointer tuple's elements
    # when pulled, so the writes made after its creation show.
    g = relay(p[0].n * 1000 + p[1].n + i for i in range(2))  # tpyc: ok
    a.n += 100
    v.n += 10
    print("genexpr", list(g), a.n, v.n)


def genexpr_nested(v: Box) -> None:
    p = make_mixed(1, v)
    p = make_mixed(2, v)
    q = p
    a = p[0]
    # genexpr-nested: the inner frame reads the outer frame's captures of the
    # pointer tuple and its alias when pulled, so the writes made after the
    # outer one's creation show.
    g = relay(sum(p[0].n * 100 + q[1].n + j for j in range(2)) + i for i in range(2))  # tpyc: ok
    a.n += 100
    v.n += 10
    print("genexpr-nested", list(g), a.n, v.n)


def genexpr_in_def(v: Box) -> None:
    p = make_mixed(1, v)
    p = make_mixed(2, v)
    a = p[0]

    def k() -> int32:
        # genexpr-in-def: a nested def's generator expression reads the
        # enclosing pointer tuple through the def's capture.
        return sum(p[0].n * 100 + p[1].n + i for i in range(2))  # tpyc: ok
    a.n += 100
    v.n += 10
    print("genexpr-in-def", k(), a.n, v.n)


def capture_copy(v: Box) -> Callable[[], int32]:
    p = make_mixed(1, v)
    p = make_mixed(2, v)
    # capture-copy: the remedy the capture reject offers compiles; the lambda
    # holds a copy of the element by design, and of 'v' too
    # (BUGS.md#callable-local-lambda-copies-capture-silently).
    p0 = copy(p[0])
    return lambda: p0.n * 100 + v.n  # tpyc: ok


def lambda_comprehension(v: Box, xs: list[tuple[Box, Box]]) -> None:
    p = make_mixed(1, v)
    p = make_mixed(2, v)
    # lambda-comprehension: the lambda captures the comprehension's own 'p',
    # not the pointer tuple, so it is not refused; it holds a copy of that
    # 'p' (BUGS.md#callable-local-lambda-copies-capture-silently), and each
    # lambda keeps its own element where CPython's late-bind the last one
    # (the "Comprehension form" face of the BUGS.md loop-variable capture
    # entry); one element and no write keep both out of the output.
    fs: list[Callable[[], int32]] = [lambda: p[1].n for p in xs]  # tpyc: ok
    p[0].n += 100
    print("lambda-comprehension", fs[0](), p[0].n)


def walk(b: Bag) -> Iterator[int32]:
    for x in b.xs:
        yield x


def walk_rebind(v: Bag) -> None:
    p = make_bag(1, v)
    p = make_bag(2, v)
    for i in range(1):
        for x in walk(p[0]):
            # walk-rebind: a rebind under a walk of its old element warns as
            # the scalar's does (BUGS.md#loop-rebind-site-slot-reuse-warned).
            p = make_bag(50 + x, v)  # tpyc: warning(/'p' will not keep the object it was given -- 'p' is rebound here and both names share its storage; bind the new value to a name of its own/)
            p[0].xs.append(7)
            print("walk-rebind", x, len(p[0].xs), p[0].xs[0])


def copy_carry(v: Box) -> None:
    p = make_mixed(0, v)
    for i in range(1):
        # 'old' is rebound on the next pass while 'p' still reaches the
        # object it was given, which reuses the site's slot
        # (BUGS.md#loop-rebind-site-slot-reuse-warned).
        old = copy(p[0])  # tpyc: warning(/'old' will not keep the object it was given/)
        # copy-carry: the new value keeps a copy of the old element, so the
        # rebind carries nothing over and has no warning of its own.
        p = make_mixed(10 + i, old)  # tpyc: ok
        p[1].n += 100
        print("copy-carry", p[0].n, p[1].n, old.n)


# The name is bound at two sites in the loop body, each with a slot of its
# own: the alias takes the second site's object, which the second site
# rebuilds on the next pass. Run once, so both runs agree.
def loop_two_site(v: Box) -> None:
    s = make_mixed(0, v)
    s = make_mixed(1, v)
    r = s
    for i in range(1):
        r = s
        s = make_mixed(3 + i, v)  # tpyc: ok
        # loop-two-site: warns as the one-site rebind does.
        s = make_mixed(5 + i, v)  # tpyc: warning(/'r' will not keep the object it was given -- 's' is rebound here and both names share its storage; bind the new value to a name of its own/)
    r[0].n += 100
    print("loop-two-site", r[0].n, s[0].n)


# The alias reaches the loop's object through a second name (`r = q`,
# `q = s`).
def loop_two_site_two_hop(v: Box, c: bool) -> None:
    p = make_mixed(1, v)
    p = make_mixed(2, v)
    q = p
    r = q
    s = make_mixed(0, v)
    for i in range(1):
        r = q
        r[0].n += 10
        s = make_mixed(3 + i, v)  # tpyc: ok
        # loop-two-site-two-hop: 'r' reaches the slot through 'q'.
        s = make_mixed(5 + i, v)  # tpyc: warning(/'r' will not keep the object it was given/)
        if c:
            q = s
        else:
            q = p
    r[0].n += 100
    q[0].n += 1000
    print("loop-two-site-two-hop", r[0].n, q[0].n, s[0].n, p[0].n)


# Nothing reads an alias after the two in-loop sites: no warning.
def loop_two_site_silent(v: Box) -> None:
    s = make_mixed(0, v)
    s = make_mixed(1, v)
    for i in range(2):
        # loop-two-site-silent: only 's' itself reads its objects.
        s = make_mixed(3 + i, v)  # tpyc: ok
        s = make_mixed(5 + i, v)  # tpyc: ok
        s[0].n += 100
        print("loop-two-site-silent", s[0].n)


# The `while` spelling of the two in-loop sites. Run once, so both runs agree.
def loop_two_site_while(v: Box) -> None:
    s = make_mixed(0, v)
    s = make_mixed(1, v)
    r = s
    i = 0
    while i < 1:
        r = s
        s = make_mixed(3 + i, v)  # tpyc: ok
        # loop-two-site-while: warns at the second site, whose slot 'r' holds.
        s = make_mixed(5 + i, v)  # tpyc: warning(/'r' will not keep the object it was given/)
        i += 1
    r[0].n += 100
    print("loop-two-site-while", r[0].n, s[0].n)


# Three in-loop sites, the alias taken after the second: its object lives in
# the second site's slot, and the alias is rebound before that site runs
# again, so nothing it still reads is rebuilt: no warning.
def loop_three_site(v: Box) -> None:
    s = make_mixed(0, v)
    s = make_mixed(1, v)
    r = s
    t = s
    for i in range(1):
        # loop-three-site: 'r' holds the second site's object here.
        s = make_mixed(10 + i, v)  # tpyc: ok
        if i > 0:
            print("loop-three-site-iter", r[0].n, t[0].n)
        s = make_mixed(20 + i, v)
        r = s
        s = make_mixed(30 + i, v)
        t = s
    r[0].n += 100
    print("loop-three-site", r[0].n, t[0].n, s[0].n)


# The alias is read only before the two in-loop sites: no warning.
def loop_two_site_read_before(v: Box) -> None:
    s = make_mixed(0, v)
    s = make_mixed(1, v)
    r = s
    for i in range(2):
        r = s
        r[0].n += 100
        print("loop-two-site-read-before", r[0].n, s[0].n)
        # loop-two-site-read-before: nothing reads 'r' after the sites.
        s = make_mixed(3 + i, v)  # tpyc: ok
        s = make_mixed(5 + i, v)  # tpyc: ok
    print("loop-two-site-read-before", s[0].n)


# The rebound parameter's object dies with the rebind -- warned as an owned
# parameter never consumed; the rebind's move is the subject (last-use-rebind).
def last_use_rebind(q: tuple[Own[Box], Box], v: Box) -> None:  # tpyc: warning(/owned tuple param 'q' is never consumed/)
    r = make_mixed(7, v)
    # last-use-rebind: the declared slot's check, run after the body, reads
    # the flow state of its site: 'r' is dead here, so it moves
    # (`q = std::move(r);`) and no copy warns.
    q = r  # tpyc: ok
    q[0].n += 100
    q[1].n += 1
    print("last-use-rebind", q[0].n, v.n)


# The rebound parameters below of a copyable element type but the first die
# with their rebinds -- warned as owned parameters never consumed; each
# rebind is the subject.
def branch_last_use_rebind(q: Own[tuple[Box, Box]], c: bool) -> None:
    if c:
        r = pair(7)
        # branch-last-use-rebind: the check after the body reads the flow
        # state of the site, where 'r' is owned and dead: no copy warns.
        q = r  # tpyc: ok
    q[0].n += 100
    print("branch-last-use-rebind", q[0].n, q[1].n)


def try_last_use_rebind(q: tuple[Own[Box], Box], v: Box) -> None:  # tpyc: warning(/owned tuple param 'q' is never consumed/)
    try:
        r = make_mixed(7, v)
        # try-last-use-rebind: 'r' is declared ahead of the try, and such a
        # local is not moved out of, so the rebind copies its object, warned
        # (BUGS.md#own-param-last-use-rebind-copies).
        q = r  # tpyc: warning(/copies tuple\[Own\[Box\], Box\] into owned storage; use copy\(\) to make this explicit/)
    finally:
        print("try-last-use-rebind finally")
    q[0].n += 100
    q[1].n += 1
    print("try-last-use-rebind", q[0].n, v.n)


def first(b: Box) -> Box:
    return b


def read_after_rebind(q: tuple[Own[Box], Box], v: Box) -> None:  # tpyc: warning(/owned tuple param 'q' is never consumed/)
    r = make_mixed(7, v)
    s = first(r[0])
    # read-after-rebind: 's' still reads the object of 'r' after the rebind,
    # so the rebind copies it, warned as at the site. 's.n' is left out of
    # the output on purpose: the warned copy makes it differ from CPython.
    q = r  # tpyc: warning(/copies tuple\[Own\[Box\], Box\] into owned storage; use copy\(\) to make this explicit/)
    q[0].n += 100
    print("read-after-rebind", q[0].n, s.n > 0)


def nocopy_last_use_rebind(q: tuple[Own[Tok], Box], v: Box) -> None:
    r = make_tok(7, v)
    # nocopy-last-use-rebind: the move takes the uncopyable element over.
    q = r  # tpyc: ok
    q[0].n += 100
    q[1].n += 1
    print("nocopy-last-use-rebind", q[0].n, v.n)


def gen_last_use_rebind(q: tuple[Own[Tok], Box], v: Box) -> Iterator[int32]:
    r = make_tok(7, v)
    # gen-last-use-rebind: the same move in a generator body.
    q = r  # tpyc: ok
    q[0].n += 100
    q[1].n += 1
    yield q[0].n
    yield v.n


# The scalar twins of last-use-rebind: the same move into an owned name; the
# copyable parameter's object dies with the rebind, warned as never consumed.
def scalar_last_use_rebind(q: Own[Box]) -> None:  # tpyc: warning(/Own\[Box\] param 'q' is never consumed/)
    r = Box(7)
    # scalar-last-use-rebind: 'r' is dead here, so it moves.
    q = r  # tpyc: ok
    q.n += 100
    print("scalar-last-use-rebind", q.n)


def scalar_nocopy_last_use_rebind(q: Own[Tok]) -> None:
    r = Tok(7)
    # scalar-nocopy-last-use-rebind: the move takes the uncopyable object
    # over.
    q = r  # tpyc: ok
    q.n += 100
    print("scalar-nocopy-last-use-rebind", q.n)


def scalar_gen_last_use_rebind(q: Own[Tok]) -> Iterator[int32]:
    r = Tok(7)
    # scalar-gen-last-use-rebind: the same move in a generator body.
    q = r  # tpyc: ok
    q.n += 100
    yield q.n


async def async_last_use_rebind(q: tuple[Own[Tok], Box], v: Box) -> int32:
    r = make_tok(7, v)
    await asyncio.sleep(0)
    # async-last-use-rebind: the same move into a coroutine frame's field.
    q = r  # tpyc: ok
    await asyncio.sleep(0)
    q[0].n += 100
    q[1].n += 1
    return q[0].n


# The loop sections print only what both runs agree on: the next pass reads
# 'r' again, so each rebind copies, warned.
def loop_rebind(q: tuple[Own[Box], Box], v: Box) -> None:  # tpyc: warning(/owned tuple param 'q' is never consumed/)
    r = make_mixed(7, v)
    for i in range(2):
        # loop-rebind: 'r' is bound outside the loop and read on every pass.
        q = r  # tpyc: warning(/copies tuple\[Own\[Box\], Box\] into owned storage; use copy\(\) to make this explicit/)
    print("loop-rebind", q[0].n, r[0].n)


def scalar_loop_rebind(q: Own[Box], k: int32) -> None:  # tpyc: warning(/Own\[Box\] param 'q' is never consumed/)
    r = Box(7)
    while k > 0:
        # scalar-loop-rebind: the scalar twin, under a `while`.
        q = r  # tpyc: warning(/copies Box into owned storage; use copy\(\) to make this explicit/)
        k -= 1
    print("scalar-loop-rebind", q.n, r.n)


def lambda_param(v: Box) -> None:
    p = make_mixed(1, v)
    p = make_mixed(2, v)
    # lambda-param: the lambda's own parameter 'p' shadows the pointer
    # tuple, so nothing is captured and nothing is refused.
    f: Callable[[int32], int32] = lambda p: p + 1  # tpyc: ok
    p[0].n += 100
    print("lambda-param", f(3), p[0].n)


def mixed_bindings(h: HB, v: Box) -> None:
    p = make_mixed(1, v)
    p = make_mixed(2, v)
    q = p
    q[0].n += 100
    # mixed-bindings: 'q' owns nothing at either binding, so it is a pointer
    # tuple holding 'p''s objects above and the field's objects here.
    q = h.bp  # tpyc: ok
    h.bp[0].n += 10
    q[1].n += 1000
    print("mixed-bindings", q[0].n, q[1].n, p[0].n, h.bp[1].n)


def mixed_bindings_reverse(h: HB, v: Box) -> None:
    p = make_mixed(1, v)
    p = make_mixed(2, v)
    q = h.bp
    h.bp[0].n += 10
    q[0].n += 1000
    # mixed-bindings-reverse: the same bindings in the other order.
    q = p  # tpyc: ok
    q[0].n += 100
    print("mixed-bindings-reverse", q[0].n, p[0].n, h.bp[0].n)


def mixed_bindings_read(h: HB, v: Box) -> None:
    p = make_mixed(1, v)
    p = make_mixed(2, v)
    q = p
    p[0].n += 100
    print("mixed-bindings-read", q[0].n)
    # mixed-bindings-read: nothing writes through 'h', yet the binding takes
    # the field's address, so 'h' stays a mutable reference.
    q = h.bp  # tpyc: ok
    print("mixed-bindings-read", q[0].n, p[0].n)


def field_repoint_loop(h: HB, v: Box) -> None:
    p = make_mixed(1, v)
    p = make_mixed(2, v)
    q = p
    for i in range(2):
        q[0].n += 100
        # field-repoint-loop: 'q' holds 'p''s objects on the first pass and
        # the parameter's field after it.
        q = h.bp  # tpyc: ok
    h.bp[0].n += 10
    q[1].n += 1000
    print("field-repoint-loop", q[0].n, q[1].n, p[0].n, h.bp[1].n)


def field_repoint_branch(h: HB, v: Box, c: bool) -> None:
    p = make_mixed(1, v)
    p = make_mixed(2, v)
    if c:
        q = p
    else:
        # field-repoint-branch: one arm binds 'p''s objects, the other the
        # parameter's field.
        q = h.bp  # tpyc: ok
    q[0].n += 100
    h.bp[0].n += 10
    print("field-repoint-branch", q[0].n, p[0].n, h.bp[0].n)


class HM:
    bp: tuple[Box, Box]

    def __init__(self) -> None:
        self.bp = (Box(50), Box(60))

    def repoint(self, v: Box) -> None:
        p = make_mixed(1, v)
        p = make_mixed(2, v)
        q = p
        q[0].n += 100
        # field-repoint-method: 'q' re-points at the receiver's field.
        q = self.bp  # tpyc: ok
        self.bp[0].n += 10
        q[1].n += 1000
        print("field-repoint-method", q[0].n, q[1].n, p[0].n, self.bp[1].n)


def literal_after_alias(v: Box) -> None:
    p = make_mixed(1, v)
    p = make_mixed(2, v)
    q = p
    q[0].n += 100
    # literal-after-alias: 'q' declared from 'p' owns element 0 of the
    # literal, the warned copy of 'v' the name-element section describes;
    # only the borrowed element 1 is observed.
    q = (v, v)  # tpyc: warning(/copies Box into owned storage \(tuple element 0\)/)
    q[1].n += 1
    print("literal-after-alias", p[0].n, q[1].n, v.n)


def alias_after_literal(v: Box) -> None:
    p = make_mixed(1, v)
    p = make_mixed(2, v)
    q = (v, v)
    q[0].n += 1
    # alias-after-literal: a literal of names owns nothing, so 'q' holds
    # 'v' and then 'p''s objects.
    q = p  # tpyc: ok
    q[0].n += 100
    print("alias-after-literal", p[0].n, q[0].n, v.n)


def ternary_alias(v: Box, w: Box, c: bool) -> None:
    p = make_mixed(1, v)
    p = make_mixed(2, v)
    s = make_mixed(5, w)
    s = make_mixed(6, w)
    # ternary-alias: one binding from either pointer tuple.
    q = p if c else s  # tpyc: ok
    q[0].n += 100
    print("ternary-alias", q[0].n, p[0].n, s[0].n)


def ternary_param_roots(h: HB, g: HB, v: Box, c: bool) -> None:
    p = make_mixed(1, v)
    p = make_mixed(2, v)
    q = p
    q = h.bp
    r = p
    r = g.bp
    # ternary-param-roots: each arm an alias re-pointed at a parameter's
    # field, so the select holds parameter storage either way.
    u = q if c else r  # tpyc: ok
    u[0].n += 100
    print("ternary-param-roots", u[0].n, h.bp[0].n, g.bp[0].n, p[0].n)


def owning_rebind_alias(v: Box) -> None:
    p = make_mixed(1, v)
    p = make_mixed(2, v)
    q = p
    q[0].n += 100
    # owning-rebind-alias: an owning binding makes 'q' a pointer tuple of
    # its own; 'p''s object it still holds gets no in-place write.
    q = make_mixed(3, v)  # tpyc: ok
    q[0].n += 1000
    print("owning-rebind-alias", p[0].n, q[0].n)


class HCtor:
    bp: tuple[Box, Box]

    def __init__(self, v: Box) -> None:
        self.bp = (Box(50), Box(60))
        p = make_mixed(1, v)
        p = make_mixed(2, v)
        # ctor-self-root: the constructor's receiver is a root it admits.
        p = self.bp  # tpyc: ok
        p[0].n += 100
        print("ctor-self-root", p[0].n, self.bp[0].n)


def nested_own_param(v: Box) -> None:
    def inner(h: HB, w: Box) -> int32:
        p = make_mixed(1, w)
        p = make_mixed(2, w)
        # nested-own-param: the nested def's own parameter is a root it
        # admits (an enclosing body's one is refused: captured).
        p = h.bp  # tpyc: ok
        p[0].n += 100
        return h.bp[0].n

    hb = HB()
    print("nested-own-param", inner(hb, v), hb.bp[0].n)


class TwoHop:
    xs: list[Box]

    def __init__(self, i: int32) -> None:
        self.xs = [Box(i), Box(i + 1)]


class Named:
    s: str
    n: int32

    def __init__(self, i: int32) -> None:
        self.s = "abcdefghijklmnopqrstuvwxyz0123456789" + str(i)
        self.n = i


class Lab(ValueType):
    lab: StrView

    def __init__(self, lab: StrView) -> None:
        self.lab = lab


class Labelled:
    s: str
    w: Lab

    def __init__(self, src: str) -> None:
        self.s = src + "!"
        self.w = Lab(self.s)


class Keeper:
    p: Ptr[Box]

    def __init__(self, b: Box) -> None:
        self.p = take_ptr(b)

    def save(self, b: Box) -> int32:
        self.p = take_ptr(b)
        return 1


def pick_box(b: Box) -> tuple[Box, int32]:
    return (b, 0)


# A holder bound by a path into an owned element that no tracked loan records
# keeps the old element's object: the rebind takes storage of its own.
def untracked_two_hop(c: bool) -> None:
    t = (TwoHop(1), 5)
    # untracked-two-hop: two hops into element 0.
    saved = t[0].xs[1]  # tpyc: ok
    if c:
        t = (TwoHop(10), 6)
    saved.n += 100
    print("untracked-two-hop", saved.n, t[0].xs[1].n)


def untracked_strview(c: bool) -> None:
    t = (Named(1), 5)
    # untracked-strview: the view points into element 0's string.
    v: StrView = t[0].s  # tpyc: ok
    if c:
        t = (Named(2), 6)
    print("untracked-strview", v, t[1])


def untracked_value_view(c: bool, a: str, b: str) -> None:
    t = (Labelled(a), 5)
    # untracked-value-view: a value whose view points into element 0.
    w = t[0].w  # tpyc: ok
    if c:
        t = (Labelled(b), 6)
    print("untracked-value-view", w.lab, t[0].w.lab)


def untracked_fresh_view(c: bool) -> None:
    t = (Named(1), 5)
    # untracked-fresh-view: a new value around a view of element 0.
    w = Lab(t[0].s)  # tpyc: ok
    if c:
        t = (Named(2), 6)
    print("untracked-fresh-view", w.lab)


def untracked_match_capture(c: bool) -> None:
    t = (TwoHop(1), 5)
    # untracked-match-capture: the capture is bound two hops in.
    match t[0].xs[1]:  # tpyc: ok
        case Box() as saved:
            if c:
                t = (TwoHop(10), 6)
            saved.n += 100
            print("untracked-match-capture", saved.n, t[1])


def untracked_for_unpack(c: bool) -> None:
    t = (TwoHop(1), 5)
    # untracked-for-unpack: the loop target that keeps the borrow.
    for i, saved in enumerate(t[0].xs):  # tpyc: ok
        if i == 1:
            if c:
                # The loop target's loan is filed at the loop head, which a
                # rebind's back edge reaches: a conservative warning
                # (BUGS.md#loop-slot-reuse-false-warning).
                t = (TwoHop(10), 6)  # tpyc: warning(/'saved' will not keep the object it was given/)
            saved.n += 100
            print("untracked-for-unpack", saved.n, t[0].xs[1].n)


def untracked_literal_unpack(c: bool) -> None:
    t = (TwoHop(1), 5)
    h = TwoHop(20)
    # untracked-literal-unpack: each target of a literal unpack.
    saved, a = (t[0].xs[1], h.xs[0])  # tpyc: ok
    # The same path as the SECOND target.
    b, kept = (h.xs[1], t[0].xs[1])  # tpyc: ok
    if c:
        t = (TwoHop(10), 6)
    saved.n += 100
    kept.n += 1000
    print("untracked-literal-unpack", saved.n, a.n, kept.n, b.n,
          t[0].xs[1].n)


def untracked_chained(c: bool) -> None:
    t = (TwoHop(1), 5)
    # untracked-chained: each name target of a chained assignment holds the
    # old element two hops in.
    x = saved = t[0].xs[1]  # tpyc: ok
    if c:
        t = (TwoHop(10), 6)
    saved.n += 100
    x.n += 1000
    print("untracked-chained", saved.n, x.n, t[0].xs[1].n)


def untracked_call_unpack(c: bool) -> None:
    t = (TwoHop(1), 5)
    # untracked-call-unpack: a call lends the element's path to the target.
    saved, k = pick_box(t[0].xs[1])  # tpyc: ok
    if c:
        t = (TwoHop(10), 6)
    saved.n += 100
    print("untracked-call-unpack", saved.n + k, t[0].xs[1].n)


def retain_binding(c: bool) -> None:
    k = Keeper(Box(99))
    t = (Box(1), 0)
    # retain-binding: the callee keeps a pointer to the element.
    n = k.save(t[0])  # tpyc: ok
    if c:
        t = (Box(10), 0)
    k.p.n += 100
    print("retain-binding", n, k.p.n, t[0].n)


def retain_statement(c: bool) -> None:
    k = Keeper(Box(99))
    t = (Box(1), 0)
    # retain-statement: the same call as a statement.
    k.save(t[0])  # tpyc: ok
    if c:
        t = (Box(10), 0)
    k.p.n += 100
    print("retain-statement", k.p.n, t[0].n)


def tuple_view(c: bool) -> None:
    t = (Named(1), 0)
    # tuple-view: a tuple whose element views element 0's string.
    u = (Lab(t[0].s), 0)  # tpyc: ok
    if c:
        t = (Named(10), 0)
    print("tuple-view", u[0].lab)


def range_field(c: bool) -> None:
    t = (Named(1), 0)
    # range-field: the loop bound is read off the element before the loop.
    for i in range(t[0].n):  # tpyc: ok
        if c:
            t = (Named(10), 0)
        print("range-field", i, t[0].n)


def copied_elem(c: bool) -> None:
    t = (Named(1), 0)
    # copied-elem: a list of numbers read off the element.
    copied = [t[0].n, 7]  # tpyc: ok
    if c:
        t = (Named(10), 0)
    copied.append(t[0].n)
    print("copied-elem", copied)


def mapped_elem(c: bool) -> None:
    t = (Named(1), 0)
    # mapped-elem: a dict of strings read off the element.
    v = {1: t[0].s}  # tpyc: ok
    if c:
        t = (Named(10), 0)
    v[2] = t[0].s
    print("mapped-elem", v[1][-1], v[2][-2:])


def main() -> None:
    v = Box(5)
    w = Box(6)
    retain_early(Box(5))
    retain_late(Box(5))
    function(v, w)
    in_place_then_own(v)
    rhs_retains(Box(7))
    eval_order()
    hoisted(True)
    deleted(Box(3))
    literal(Box(4))
    owned_call()
    alias_after(Box(8))
    xs = [(Box(1), Box(2))]
    subscript_source(xs, Box(7))
    inline_then_borrow([Box(90)], Box(2))
    b = Box(10)
    arg_last_use(True, b)
    r = return_last_use(True)
    r[1].n += 1
    print("return-last-use", r[0].n, r[1].n)
    bag = Bag(7)
    q = return_borrowed(bag)
    print("return-borrowed", len(bag.xs), len(q[0].xs), len(q[1].xs))
    own_arg(Box(12))
    consuming(Box(11))
    Holder(v).method(v)
    try_finally(v)
    with_body(v)
    mutable_sources(Holder(v))
    name_element(Box(1), Box(2))
    elem_arg(Box(7))
    take_pointer(Box(13))
    elem_reseat(Box(1), Box(2))
    for_hoist(Box(0))
    name_bound(Box(2), True)
    oe = own_elem_return(True)
    print("own-elem-return", oe[0].n, oe[1])
    print("constructor", Built(Box(4)).seen)
    matched(1, Box(5))
    try:
        print("error-return", fallible_rebind(Box(6)))
    except Fail:
        print("error-return failed")
    unpack_alias(Box(8))
    branch_once(True, Box(5))
    closure(Box(5))
    comprehension(Box(5))
    nonlocal_write(Box(1))
    va = Box(1)
    print("async", asyncio.run(frame_body(va)), va.n)
    field_repoint(HB())
    written_then_field(HB(), Box(1))
    match_capture(Box(3))
    hoist_alias(Box(0), True)
    hoist_alias(Box(0), False)
    mixed_arg(Bag(0))
    except_binder(Box(1))
    except_binder_branches(True)
    borrowed_place([Box(4)], Box(1))
    field_place(HB(), Box(1))
    gen_loop(Box(5))
    swap_names(Box(5))
    alias_order_early(Box(5))
    alias_order_late(Box(5))
    alias_flat(Box(3), True)
    alias_nested(Box(3), True)
    genexpr_read(Box(5))
    genexpr_nested(Box(5))
    genexpr_in_def(Box(5))
    cb = capture_copy(Box(5))
    print("capture-copy", cb())
    lambda_comprehension(Box(5), [(Box(3), Box(4))])
    walk_rebind(Bag(0))
    copy_carry(Box(5))
    loop_two_site(Box(5))
    loop_two_site_two_hop(Box(5), True)
    loop_two_site_two_hop(Box(5), False)
    loop_two_site_silent(Box(5))
    loop_two_site_while(Box(5))
    loop_three_site(Box(5))
    loop_two_site_read_before(Box(5))
    last_use_rebind(make_mixed(1, Box(2)), Box(6))
    branch_last_use_rebind(pair(1), True)
    try_last_use_rebind(make_mixed(1, Box(2)), Box(6))
    read_after_rebind(make_mixed(1, Box(2)), Box(6))
    nocopy_last_use_rebind(make_tok(1, Box(2)), Box(6))
    print("gen-last-use-rebind", list(gen_last_use_rebind(make_tok(1, Box(2)), Box(6))))
    scalar_last_use_rebind(Box(1))
    scalar_nocopy_last_use_rebind(Tok(1))
    print("scalar-gen-last-use-rebind", list(scalar_gen_last_use_rebind(Tok(1))))
    vt = Box(6)
    print("async-last-use-rebind", asyncio.run(async_last_use_rebind(make_tok(1, Box(2)), vt)), vt.n)
    loop_rebind(make_mixed(1, Box(2)), Box(6))
    scalar_loop_rebind(Box(1), 2)
    lambda_param(Box(5))
    mixed_bindings(HB(), Box(1))
    mixed_bindings_reverse(HB(), Box(1))
    mixed_bindings_read(HB(), Box(1))
    field_repoint_loop(HB(), Box(1))
    field_repoint_branch(HB(), Box(1), True)
    field_repoint_branch(HB(), Box(1), False)
    hm = HM()
    # A local argument: BUGS.md#method-rvalue-arg-at-mutable-ref-param.
    hb = Box(1)
    hm.repoint(hb)
    literal_after_alias(Box(5))
    alias_after_literal(Box(5))
    ternary_alias(Box(3), Box(4), True)
    ternary_alias(Box(3), Box(4), False)
    ternary_param_roots(HB(), HB(), Box(3), True)
    ternary_param_roots(HB(), HB(), Box(3), False)
    owning_rebind_alias(Box(3))
    HCtor(Box(3))
    nested_own_param(Box(3))
    untracked_two_hop(True)
    untracked_strview(True)
    untracked_value_view(True, "a" * 40, "b" * 40)
    untracked_fresh_view(True)
    untracked_match_capture(True)
    untracked_for_unpack(True)
    untracked_literal_unpack(True)
    untracked_chained(True)
    untracked_call_unpack(True)
    retain_binding(True)
    retain_statement(True)
    tuple_view(True)
    range_field(True)
    copied_elem(True)
    mapped_elem(True)


main()
