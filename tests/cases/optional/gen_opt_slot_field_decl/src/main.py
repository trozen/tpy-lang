# The GENERATOR / async position of a pointer-repr `Optional[T]` local first
# bound from an optional FIELD off a dying temporary (the straight-line
# function position is `opt_slot_field_rebind`). A frame local's
# materialization storage has to be a frame FIELD -- an inline slot dies at the
# first suspension -- so the whole `std::optional<Point>` lands in the
# prescanned field and the pointer lifts off it
# (`__ptr_slot_f0 = make_holder(x).value; p = optional_to_ptr(__ptr_slot_f0);`).
# The receiver is a temporary, so the field COPIES the optional out of it --
# intended here, and the only sound choice: a pointer into the temporary would
# dangle at the end of the statement. A NAMED holder local is the other half:
# an lvalue receiver, so the pointer aims into the holder and `gen_named`
# observes the write back through it. Each section writes through `p` AFTER a
# suspension and reads it back, so a slot that did not survive the yield shows
# up as a wrong tag.
import asyncio
from typing import Iterator

from tpy import int32, Own


class Point:
    tag: str

    def __init__(self, tag: str) -> None:
        self.tag = tag


class Holder:
    value: Point | None

    def __init__(self, x: int32) -> None:
        self.value = None
        if x > 0:
            self.value = Point("live")


def make_holder(x: int32) -> Own[Holder]:
    return Holder(x)


# free generator: the field is present, and the pointer still aims at the
# frame's own storage after the suspension
def gen(x: int32) -> Iterator[str]:
    p: Point | None = make_holder(x).value  # tpyc: ok
    yield "none" if p is None else p.tag
    if p is not None:
        p.tag = "bumped"
    yield "none" if p is None else p.tag


# async: the coroutine sibling
async def coro(x: int32) -> str:
    p: Point | None = make_holder(x).value  # tpyc: ok
    await asyncio.sleep(0)
    if p is None:
        return "none"
    p.tag = "coro"
    return p.tag


# the same local REBOUND after the suspension: the reseat takes a frame slot
# of its own, so the empty-source path is not a write through a null pointer
def gen_rebind(x: int32) -> Iterator[str]:
    p: Point | None = make_holder(x).value  # tpyc: ok
    yield "none" if p is None else p.tag
    p = Point("set")  # tpyc: ok
    yield "none" if p is None else p.tag


# a NAMED holder local: the source is an LVALUE, so the pointer aims INTO the
# holder and the write through `p` is observable through `h` afterwards
def gen_named(x: int32) -> Iterator[str]:
    h = make_holder(x)
    p: Point | None = h.value  # tpyc: ok
    yield "none" if p is None else p.tag
    if p is not None:
        p.tag = "named"
    v = h.value
    yield "none" if v is None else v.tag


# the decl inside a `try` body
def gen_try(x: int32) -> Iterator[str]:
    try:
        p: Point | None = make_holder(x).value  # tpyc: ok
        yield "none" if p is None else p.tag
        if p is not None:
            p.tag = "try"
        yield "none" if p is None else p.tag
    finally:
        print("try done")


# the decl inside a branch
def gen_branch(x: int32) -> Iterator[str]:
    if x >= 0:
        p: Point | None = make_holder(x).value  # tpyc: ok
        yield "none" if p is None else p.tag
        if p is not None:
            p.tag = "branch"
        yield "none" if p is None else p.tag


# the decl inside a loop body
def gen_loop(x: int32) -> Iterator[str]:
    for _ in range(1):
        p: Point | None = make_holder(x).value  # tpyc: ok
        yield "none" if p is None else p.tag
        if p is not None:
            p.tag = "loop"
        yield "none" if p is None else p.tag


class Runner:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    # generator METHOD: same decl with a receiver in the frame
    def walk(self) -> Iterator[str]:
        p: Point | None = make_holder(self.n).value  # tpyc: ok
        yield "none" if p is None else p.tag
        if p is not None:
            p.tag = "method"
        yield "none" if p is None else p.tag


def main() -> None:
    for s in gen(3):
        print("gen:", s)
    for s in gen(0):
        print("gen empty:", s)
    print("coro:", asyncio.run(coro(3)), asyncio.run(coro(0)))
    for s in gen_rebind(0):
        print("rebind:", s)
    for s in gen_rebind(3):
        print("rebind live:", s)
    for s in gen_named(3):
        print("named:", s)
    for s in gen_named(0):
        print("named empty:", s)
    for s in gen_try(3):
        print("try:", s)
    for s in gen_branch(3):
        print("branch:", s)
    for s in gen_loop(3):
        print("loop:", s)
    for s in Runner(5).walk():
        print("method:", s)
    for s in Runner(0).walk():
        print("method empty:", s)


main()
