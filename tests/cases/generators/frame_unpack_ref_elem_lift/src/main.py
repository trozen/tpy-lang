# `a, b = <lvalue>` inside a RESUMABLE body, where the tuple has a reference
# element the target aliases as a frame field: the tuple must lift through
# `tuple_to_pointer` off the source lvalue, never
# into a by-value `auto` holder -- that holder copies the reference element
# (so a write through the alias misses the caller's object) and dies at the
# end of its case block (so every post-suspension read through the alias is
# use-after-scope). The source may be any field / subscript chain bottoming
# out in storage the FRAME holds -- a captured param, a frame-resident local,
# a pointer-form alias local, a loop var -- so the last four sections root the
# lift one or more hops out. Each section mutates through the alias AFTER a
# suspension and reads the change back through the container, which a copy
# cannot produce -- except `gen_readonly`, which writes nothing and so
# takes the CONST half of the same lift (`const Box*` element pointers off a
# const-borrow capture), and `gen_pack`, whose root is an unmutated `*args`
# pack -- a frame binding whose const-ness is the pack's ELEMENT flip, not a
# param verdict. The mutating sections are the COPY half only: the
# use-after-scope half has no runtime witness here -- reading a destroyed
# holder is UB, so what pins it is the COMMITTED RENDER (the lift is off the
# source lvalue, never into an `auto` holder); ASAN is the only other way to
# see it.
import asyncio
from typing import Iterator

from tpy import int32, Ptr, readonly


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Holder:
    pair: tuple[int32, Box]

    def __init__(self, b: Box) -> None:
        # the field owns its element; the generator then aliases THAT Box
        self.pair = (1, b)  # tpyc: warning(/copies Box into field/)


# generator, container-element source: the alias must survive the yield
def gen_subscript(pairs: list[tuple[int32, Box]]) -> Iterator[int32]:
    a, b = pairs[0]  # tpyc: ok
    yield a
    b.n += 1
    yield b.n


# generator, record-FIELD source: the same lift off the member lvalue
def gen_field(h: Holder) -> Iterator[int32]:
    a, b = h.pair  # tpyc: ok
    yield a
    b.n += 10
    yield b.n


# generator, container LOCAL receiver: the source is a frame slot, so the
# deref'd read is the lvalue the element pointers come off
def gen_local() -> Iterator[int32]:
    # annotated: a bare literal would take the fixed-size Array form, which
    # is a different (still rejecting) source family
    own: list[tuple[int32, Box]] = [(4, Box(5))]
    a, b = own[0]  # tpyc: ok
    yield a
    b.n += 100
    yield own[0][1].n


# generator, try/finally position: the unpack sits in a guarded region
def gen_try(pairs: list[tuple[int32, Box]]) -> Iterator[int32]:
    try:
        a, b = pairs[0]  # tpyc: ok
        yield a
        b.n += 1000
        yield b.n
    finally:
        print("try/finally done")


# generator, VALUE-element tuple: no reference element, so no lift -- the
# holder copy is the right render and the scalars come out by value
def gen_value(pairs: list[tuple[int32, int32]]) -> Iterator[int32]:
    a, b = pairs[0]  # tpyc: ok
    yield a
    yield b


def borrow_pair(h: Holder) -> tuple[int32, Box]:
    return h.pair


# generator, CALL source: the result is already borrow form (its slots are
# pointers into the caller's storage), so the by-value holder is only a
# pointer copy and the alias still reaches the caller's Box
def gen_call(h: Holder) -> Iterator[int32]:
    a, b = borrow_pair(h)  # tpyc: ok
    yield a
    b.n += 7
    yield b.n


# generator, READ-ONLY sibling of gen_subscript: nothing is written through
# the alias, so the param is a const borrow and the lift spells the element
# pointers `const Box*` -- the same statement on the other side of the
# capture's const verdict (`generators/frame_const_source_slots` covers the
# derived slots)
def gen_readonly(pairs: list[tuple[int32, Box]]) -> Iterator[int32]:
    a, b = pairs[0]  # tpyc: ok
    yield a
    yield b.n


# generator, CHAINED source: a field off a subscript -- any number of hops
# lift off the chain, so the root need not be the immediate receiver
def gen_pack(*hs: Holder) -> Iterator[int32]:
    # `*args` PACK root: an unmutated pack is captured `varargs<const Holder>`,
    # so the lift off it spells `const Box*` -- the pack's const-ness is its
    # element flip, which no param verdict records
    a, b = hs[0].pair  # tpyc: ok
    yield a
    yield b.n


def gen_pack_bump(*hs: Holder) -> Iterator[int32]:
    # the mutating twin keeps `varargs<Holder>`, and the write through the
    # alias after the suspension reaches the caller's Box
    a, b = hs[0].pair  # tpyc: ok
    yield a
    b.n += 10
    yield b.n


class Keeper:
    h: Holder

    def __init__(self, b: Box) -> None:
        self.h = Holder(b)

    @readonly
    def gen_ro(self) -> Iterator[int32]:
        # a `@readonly` generator METHOD: the receiver's const lives on the
        # method, in neither param-verdict set, so the frame's lifted field
        # spells `const Box*` -- and the section only reads through it
        a, b = self.h.pair  # tpyc: ok
        yield a
        yield b.n

    def gen_bump(self) -> Iterator[int32]:
        # the mutating twin in a non-readonly method: the write after the
        # suspension reaches the object the receiver names
        a, b = self.h.pair  # tpyc: ok
        yield a
        b.n += 5
        yield self.h.pair[1].n


# generator, `Ptr[readonly[T]]` root: the const is on the POINTEE, so neither
# an explicit readonly nor a param-index verdict reports it -- the frame's
# capture and the lift off it must still spell const, or the C++ build fails
# on the lift alone (the sync twin is `tuple/unpack_chained_receiver`'s
# `ptr_ro_read`)
class Grid:
    rows: list[Holder]

    def __init__(self, b: Box) -> None:
        self.rows = [Holder(b)]


def gen_ptr_ro(p: Ptr[readonly[Grid]]) -> Iterator[int32]:
    a, b = p.rows[0].pair  # tpyc: ok
    yield a
    yield b.n


# generator, const LOOP VAR: the binding's const-ness comes from the
# ITERABLE's capture, which lives in neither param-verdict set -- the frame
# classifies its own captures, so the loop var and the lift off it must come
# out `const Holder*` / `const Box*` on both readonly spellings or the C++
# build fails on the lift alone
def gen_loop_ro(g: readonly[Grid]) -> Iterator[int32]:
    for h in g.rows:
        a, b = h.pair  # tpyc: ok
        yield a
        yield b.n


def gen_loop_ptr_ro(p: Ptr[readonly[Grid]]) -> Iterator[int32]:
    for h in p.rows:
        a, b = h.pair  # tpyc: ok
        yield a
        yield b.n


def gen_chain(hs: list[Holder]) -> Iterator[int32]:
    a, b = hs[0].pair  # tpyc: ok
    yield a
    b.n += 3
    yield b.n


# generator, POINTER-FORM alias local root: `h` is a `Holder*` frame field,
# so the chain roots at storage the frame holds across the suspension
def gen_alias_root(hs: list[Holder]) -> Iterator[int32]:
    h = hs[0]
    a, b = h.pair  # tpyc: ok
    yield a
    b.n += 4
    yield b.n


# generator, LOOP VAR root: the loop var is the same pointer-form frame
# binding, re-pointed per iteration
def gen_loop_root(hs: list[Holder]) -> Iterator[int32]:
    for h in hs:
        a, b = h.pair  # tpyc: ok
        yield a
        b.n += 5
        yield b.n


# async twin of the chained source
async def coro_chain(hs: list[Holder]) -> int32:
    a, b = hs[0].pair  # tpyc: ok
    await asyncio.sleep(0)
    b.n += 6
    return a + b.n


# async, container-element source: the alias must survive the await
async def coro_subscript(pairs: list[tuple[int32, Box]]) -> int32:
    a, b = pairs[0]  # tpyc: ok
    await asyncio.sleep(0)
    b.n += 2
    return a + b.n


async def main_coro(pairs: list[tuple[int32, Box]]) -> None:
    print("async", await coro_subscript(pairs), pairs[0][1].n)


async def main_coro_chain(hs: list[Holder]) -> None:
    print("async chain", await coro_chain(hs), hs[0].pair[1].n)


def main() -> None:
    xs = [(1, Box(2))]
    for v in gen_subscript(xs):
        print("subscript", v)
    print("subscript container", xs[0][1].n)

    h = Holder(Box(20))
    for v in gen_field(h):
        print("field", v)
    print("field container", h.pair[1].n)

    for v in gen_local():
        print("local", v)

    ys = [(30, Box(31))]
    for v in gen_try(ys):
        print("try", v)
    print("try container", ys[0][1].n)

    for v in gen_value([(40, 41)]):
        print("value", v)

    h2 = Holder(Box(50))
    for v in gen_call(h2):
        print("call", v)
    print("call container", h2.pair[1].n)

    for v in gen_readonly([(70, Box(71))]):
        print("readonly", v)

    zs = [(60, Box(61))]
    asyncio.run(main_coro(zs))

    ph = Holder(Box(90))
    for v in gen_pack(ph):
        print("pack", v)
    for v in gen_pack_bump(ph):
        print("pack bump", v)
    print("pack container", ph.pair[1].n)

    chain: list[Holder] = [Holder(Box(80))]
    for v in gen_chain(chain):
        print("chain", v)
    print("chain container", chain[0].pair[1].n)

    alias: list[Holder] = [Holder(Box(90))]
    for v in gen_alias_root(alias):
        print("alias", v)
    print("alias container", alias[0].pair[1].n)

    loop: list[Holder] = [Holder(Box(100)), Holder(Box(110))]
    for v in gen_loop_root(loop):
        print("loop", v)
    print("loop container", loop[0].pair[1].n, loop[1].pair[1].n)

    pro = Grid(Box(140))
    for v in gen_ptr_ro(pro):
        print("ptr_ro", v)

    gro = Grid(Box(150))
    gro.rows.append(Holder(Box(160)))
    for v in gen_loop_ro(gro):
        print("loop_ro", v)
    for v in gen_loop_ptr_ro(gro):
        print("loop_ptr_ro", v)

    k = Keeper(Box(130))
    for v in k.gen_ro():
        print("gen_ro", v)
    for v in k.gen_bump():
        print("gen_bump", v)
    print("gen_bump container", k.h.pair[1].n)

    ws: list[Holder] = [Holder(Box(120))]
    asyncio.run(main_coro_chain(ws))


main()
