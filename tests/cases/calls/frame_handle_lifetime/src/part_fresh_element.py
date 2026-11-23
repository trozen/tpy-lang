# An element a user `__getitem__` lends out of a FRESH container, handed as
# an argument: the frame or the loop holds the container in its seat and the
# element binds by reference into it, so the frame never reads a dead bag.
import asyncio
from typing import Iterator

from tpy import Own, StrView, int32, nocopy, nomove
from tpy.mem import UninitArrayStorage

from part_borrowed_element import (Bag, Outer, bump_outer, co_outer,
                                   make_junk)


@nocopy
class Rows:
    rows: list[list[int32]]

    def __init__(self, x: int32) -> None:
        self.rows = [[x, x + 1], [x + 2]]

    def __getitem__(self, i: int32) -> list[int32]:
        return self.rows[i]


pinned_built: list[int32] = []
rows_made: list[int32] = []


# No move constructor: its seat can only be built where it is declared.
@nocopy
class Pinned:
    rows: list[list[int32]]
    storage: UninitArrayStorage[str, 1]

    def __init__(self, x: int32) -> None:
        self.storage = UninitArrayStorage[str, 1]()
        self.rows = [[x, x + 1]]
        pinned_built.append(x)

    def __getitem__(self, i: int32) -> list[int32]:
        return self.rows[i]


# `@nomove`: never relocated, so a conditional seat builds it in place.
@nomove
class Loose:
    rows: list[list[int32]]

    def __init__(self, x: int32) -> None:
        self.rows = [[x, x + 1]]

    def __getitem__(self, i: int32) -> list[int32]:
        return self.rows[i]


def make_loose(x: int32) -> Own[Loose]:
    rows_made.append(x)
    return Loose(x)


# Not movable through its `@nomove` member.
class Nested:
    loose: Loose
    rows: list[list[int32]]

    def __init__(self, x: int32) -> None:
        self.loose = Loose(x)
        self.rows = [[x, x + 1]]

    def __getitem__(self, i: int32) -> list[int32]:
        return self.rows[i]


def make_nested(x: int32) -> Own[Nested]:
    rows_made.append(x)
    return Nested(x)


def make_pinned(x: int32) -> Own[Pinned]:
    rows_made.append(x)
    return Pinned(x)


def make_bag() -> Own[Bag]:
    return Bag()


def make_rows(x: int32) -> Own[Rows]:
    rows_made.append(x)
    return Rows(x)


def same(xs: list[int32]) -> list[int32]:
    return xs


def each(xs: list[int32]) -> Iterator[int32]:
    for x in xs:
        yield x


async def ro(o: Outer) -> int32:
    await asyncio.sleep(0)
    junk = make_junk()
    return o.inner.x + len(junk) - 20


def sync_head() -> None:
    # Each loop body names its own junk: BUGS.md#generator-loops-shared-body-name-rejected.
    # A generator argument at a sync loop head.
    for x in bump_outer(Bag()[1]):  # tpyc: ok
        junk = make_junk()
        print("fresh_head", x + len(junk) - 20)
    # A borrowing function's argument at a sync loop head.
    for v in same(make_rows(5)[0]):  # tpyc: ok
        junk_same = make_junk()
        print("fresh_same", v + len(junk_same) - 20)
    # A container that cannot move, seated as the statement's own temp.
    for v in same(Pinned(7)[0]):  # tpyc: ok
        junk_pinned = make_junk()
        print("fresh_pinned", v + len(junk_pinned) - 20)


def gen_body() -> Iterator[int32]:
    # A generator argument at a loop head of a generator body.
    for x in bump_outer(make_bag()[0]):  # tpyc: ok
        junk = make_junk()
        yield x + len(junk) - 20


async def awaited() -> None:
    # An awaited coroutine's argument.
    print("fresh_await", await co_outer(Bag()[1]))  # tpyc: ok
    print("fresh_await_ro", await ro(make_bag()[0]))  # tpyc: ok
    # A generator argument and a borrowing function's argument at a loop
    # head of a coroutine body.
    for x in bump_outer(make_bag()[1]):  # tpyc: ok
        await asyncio.sleep(0)
        junk = make_junk()
        print("fresh_async_head", x + len(junk) - 20)
    for v in same(make_rows(8)[1]):  # tpyc: ok
        await asyncio.sleep(0)
        junk_same = make_junk()
        print("fresh_async_same", v + len(junk_same) - 20)


def cond_head(flag: bool) -> None:
    # A seat in a ternary arm is built in its slot when the arm is taken:
    # only the chosen container's constructor runs.
    for v in (each(Pinned(1)[0]) if flag else each(Pinned(3)[0])):  # tpyc: ok
        print("fresh_cond", v)


def cond_movable(flag: bool) -> None:
    # A movable container's seat banks into its arm: only the chosen
    # arm's factory runs.
    for v in (each(make_rows(1)[0]) if flag  # tpyc: ok
              else each(make_rows(3)[0])):
        print("fresh_cond_movable", v)
    # ... and factories' containers that cannot move: `@nomove`, through a
    # member, and with no move at all.
    for v in (each(make_loose(5)[0]) if flag  # tpyc: ok
              else each(make_loose(7)[0])):
        print("fresh_cond_loose", v)
    for v in (each(make_nested(9)[0]) if flag  # tpyc: ok
              else each(make_pinned(11)[0])):
        print("fresh_cond_nested", v)


def tick(*xs: int32) -> int32:
    pinned_built.append(100 + xs[0])
    return xs[0]


def mark(x: int32) -> int32:
    pinned_built.append(200 + x)
    return x


def cond_head_args(flag: bool) -> None:
    # A second function's in-slot seats, over arguments with side effects:
    # each argument runs once, in the taken arm, with this call's values.
    for v in (each(Pinned(tick(21))[0]) if flag  # tpyc: ok
              else each(Pinned(tick(23))[0])):
        print("fresh_cond_args", v)
    # A nested argument of the seat's constructor runs once.
    for v in each(Pinned(tick(mark(25), 26))[0]):  # tpyc: ok
        print("fresh_nested_args", v)


def mk_strs(n: int32) -> Own[list[str]]:
    return ["ab" * n + "!", "cd"]


def sv_chars(s: StrView) -> Iterator[str]:
    for c in s:
        junk = make_junk()
        yield c + str(len(junk) - 20)


async def co_sv(s: StrView) -> str:
    await asyncio.sleep(0)
    junk = make_junk()
    return str(s) + str(len(junk) - 20)


async def strs_heads() -> None:
    # A `str` element of a fresh builtin list, viewed by a generator at a
    # loop head and by an awaited coroutine: the list is held.
    out = ""
    for c in sv_chars(mk_strs(2)[0]):  # tpyc: ok
        out += c
    print("fresh_str_head", out)
    print("fresh_str_await", await co_sv(mk_strs(3)[0]))  # tpyc: ok


def main() -> None:
    cond_head(True)
    cond_head(False)
    print("fresh_cond built", pinned_built)
    rows_made.clear()
    cond_movable(True)
    cond_movable(False)
    print("fresh_cond_movable made", rows_made)
    cond_head_args(True)
    cond_head_args(False)
    print("fresh_cond_args built", pinned_built)
    asyncio.run(strs_heads())
    sync_head()
    for x in gen_body():
        print("fresh_gen_body", x)
    asyncio.run(awaited())
