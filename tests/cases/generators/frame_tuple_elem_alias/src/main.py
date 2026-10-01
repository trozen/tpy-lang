# An alias of a tuple element inside a generator / coroutine frame points at
# the element's referent: a borrowed element is already a pointer, so the
# alias copies it rather than taking the address of the element slot. The
# rejected shapes (a rebind that can run after an alias of a by-value
# element, including a `finally` rebind after a handler's alias) are pinned
# by the error_frame_*alias_rebind cases; the *_after_rebind sections are
# the rewrite their diagnostic names, the copy_* sections the other one.
import asyncio
from typing import Iterator

from tpy import copy, int32


class A:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


# generator, tuple param never written: a read-only alias.
def ro_param(p: tuple[A, A]) -> Iterator[int32]:
    a = p[1]  # tpyc: ok
    yield a.x
    yield a.x + 1


# generator, the alias is written after a suspension: the caller sees it.
def mut_param(p: tuple[A, A]) -> Iterator[int32]:
    a = p[0]  # tpyc: ok
    yield a.x
    a.x = 50
    yield a.x


# coroutine twin.
async def co_param(p: tuple[A, A]) -> int32:
    a = p[0]  # tpyc: ok
    await asyncio.sleep(0)
    a.x = 51
    return a.x


class H:
    xs: list[A]

    def __init__(self, i: int32) -> None:
        self.xs = [A(i), A(i + 1)]


class Walker:
    k: int32

    def __init__(self) -> None:
        self.k = 0

    # method generator.
    def walk(self, p: tuple[A, A]) -> Iterator[int32]:
        a = p[1]  # tpyc: ok
        yield 1
        a.x = 52
        yield a.x


# a nested def captures the alias and writes through it.
def nested(p: tuple[A, A]) -> Iterator[int32]:
    a = p[0]  # tpyc: ok

    def bump() -> None:
        a.x = 53

    yield 1
    bump()
    yield a.x


# a borrow-form tuple local of the frame.
def frame_local(r0: A, r1: A) -> Iterator[int32]:
    t = (r0, r1)
    a = t[0]  # tpyc: ok
    yield 1
    a.x = 54
    yield 2


# the borrowed element of a mixed (owned + borrowed) frame slot.
def mixed_borrowed(b: A) -> Iterator[int32]:
    t = (A(1), b)
    saved = t[1]  # tpyc: ok
    yield saved.x
    saved.x = 55
    yield b.x


# inverse: an element held by value in an owning slot the body never rebinds
# is aliased in place, so a write through the alias is seen through the slot.
def owning_no_rebind() -> Iterator[int32]:
    t = (A(1), A(2))
    saved = t[1]  # tpyc: ok
    yield saved.x
    saved.x = 7
    yield t[1].x


# inverse: the owned element of a mixed slot the body never rebinds.
def mixed_owned_no_rebind(b: A) -> Iterator[int32]:
    t = (A(1), b)
    saved = t[0]  # tpyc: ok
    yield saved.x
    saved.x = 8
    yield t[0].x


# the borrowed element of a mixed slot the body REBINDS: the element lives
# outside the slot, so the alias keeps the old object across the rebind.
def mixed_borrowed_rebound(b: A, c: A) -> Iterator[int32]:
    t = (A(1), b)
    saved = t[1]  # tpyc: ok
    yield saved.x
    t = (A(9), c)
    saved.x = 70
    yield t[1].x
    yield b.x


# every rebind of the owning slot runs BEFORE the alias is taken.
def rebind_before_alias(c: bool) -> Iterator[int32]:
    t = (A(1), A(2))
    if c:
        t = (A(9), A(8))
    saved = t[1]  # tpyc: ok
    yield saved.x
    saved.x = 5
    yield t[1].x


# the alias is taken after the last of two straight-line binds.
def alias_after_last_rebind() -> Iterator[int32]:
    t = (A(1), A(2))
    t = (A(3), A(4))
    saved = t[1]  # tpyc: ok
    yield saved.x
    saved.x = 7
    yield t[1].x


# the rebind runs in a loop BODY and the alias in the loop's `else`, which
# runs once after it: no rebind can follow the alias.
def orelse_alias() -> Iterator[int32]:
    t = (A(1), A(2))
    for i in range(1):
        t = (A(10), A(11))
    else:
        saved = t[1]  # tpyc: ok
        yield saved.x
        saved.x = 5
    yield t[1].x


# workaround 1 for a rebind after the alias: bind a copy. The copy is detached
# from the slot, so a write through it is not seen through `t`, and the
# rebind leaves it alone.
def copy_alias(c: bool) -> Iterator[int32]:
    t = (A(1), A(2))
    saved = copy(t[1])  # tpyc: ok
    yield saved.x
    saved.x = 5
    yield t[1].x
    if c:
        t = (A(9), A(8))
    yield saved.x
    yield t[1].x


# workaround 1 through a record field and a list inside the tuple.
def copy_chained(c: bool) -> Iterator[int32]:
    t = (H(1), 5)
    saved = copy(t[0].xs[1])  # tpyc: ok
    saved.x = 50
    yield t[0].xs[1].x
    if c:
        t = (H(10), 6)
    yield saved.x


# workaround 2 through a tuple inside a list slot: the alias is taken after
# the last rebind, so a write through it is seen through the slot.
def list_of_tuples_after_rebind(c: bool) -> Iterator[int32]:
    xs = [(A(1), A(2))]
    yield xs[0][1].x
    if c:
        xs = [(A(9), A(8))]
    saved = xs[0][1]  # tpyc: ok
    saved.x = 5
    yield xs[0][1].x


# workaround 1, coroutine twin. (A MIXED slot's owned element cannot be
# copied yet: BUGS.md#tuple-elem-copy-mixed-or-list-rejects.)
async def co_copy(c: bool) -> int32:
    t = (A(1), A(2))
    saved = copy(t[0])  # tpyc: ok
    await asyncio.sleep(0)
    saved.x = 6
    if c:
        t = (A(9), A(8))
    return saved.x * 100 + t[0].x


# workaround 2, coroutine twin on the owned element of a mixed slot: the
# alias is taken after the last rebind, so it aliases the new element in
# place and a write through it is seen through the slot.
async def co_alias_after_rebind(b: A, c: bool) -> int32:
    t = (A(1), b)
    await asyncio.sleep(0)
    if c:
        t = (A(9), b)
    saved = t[0]  # tpyc: ok
    await asyncio.sleep(0)
    saved.x = 7
    return t[0].x


def main() -> None:
    r0 = A(3)
    r1 = A(4)
    for v in ro_param((r0, r1)):
        print("ro_param", v)
    for v in mut_param((r0, r1)):
        print("mut_param", v)
    print("mut_param after", r0.x)
    print("co_param", asyncio.run(co_param((r0, r1))), r0.x)
    w = Walker()
    for v in w.walk((r0, r1)):
        print("method", v)
    print("method after", r1.x)
    for v in nested((r0, r1)):
        print("nested", v)
    print("nested after", r0.x)
    for v in frame_local(r0, r1):
        print("frame_local", v)
    print("frame_local after", r0.x)
    b = A(5)
    for v in mixed_borrowed(b):
        print("mixed_borrowed", v)
    print("mixed_borrowed after", b.x)
    for v in owning_no_rebind():
        print("owning_no_rebind", v)
    for v in mixed_owned_no_rebind(A(6)):
        print("mixed_owned_no_rebind", v)
    mb = A(5)
    for v in mixed_borrowed_rebound(mb, A(6)):
        print("mixed_borrowed_rebound", v)
    for v in rebind_before_alias(True):
        print("rebind_before_alias", v)
    for v in alias_after_last_rebind():
        print("alias_after_last_rebind", v)
    for v in orelse_alias():
        print("orelse_alias", v)
    for v in copy_alias(True):
        print("copy_alias", v)
    for v in copy_chained(True):
        print("copy_chained", v)
    for v in list_of_tuples_after_rebind(True):
        print("list_of_tuples_after_rebind", v)
    print("co_copy", asyncio.run(co_copy(True)))
    print("co_alias_after_rebind",
          asyncio.run(co_alias_after_rebind(A(3), True)))


main()
