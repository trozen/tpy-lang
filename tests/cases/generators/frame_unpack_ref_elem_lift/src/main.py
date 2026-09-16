# `a, b = <container element>` / `= <field>` inside a RESUMABLE body, where
# the tuple has a reference element the target aliases as a frame field: the
# tuple must lift through `tuple_to_pointer` off the source lvalue, never
# into a by-value `auto` holder -- that holder copies the reference element
# (so a write through the alias misses the caller's object) and dies at the
# end of its case block (so every post-suspension read through the alias is
# use-after-scope). Each section mutates through the alias AFTER a
# suspension and reads the change back through the container, which a copy
# cannot produce -- except the last section, which writes nothing and so
# takes the CONST half of the same lift (`const Box*` element pointers off a
# const-borrow capture). The mutating sections are the COPY half only: the
# use-after-scope half has no runtime witness here -- reading a destroyed
# holder is UB, so what pins it is the COMMITTED RENDER (the lift is off the
# source lvalue, never into an `auto` holder); ASAN is the only other way to
# see it.
import asyncio
from typing import Iterator

from tpy import int32


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


# async, container-element source: the alias must survive the await
async def coro_subscript(pairs: list[tuple[int32, Box]]) -> int32:
    a, b = pairs[0]  # tpyc: ok
    await asyncio.sleep(0)
    b.n += 2
    return a + b.n


async def main_coro(pairs: list[tuple[int32, Box]]) -> None:
    print("async", await coro_subscript(pairs), pairs[0][1].n)


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


main()
