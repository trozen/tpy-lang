# A value-repr Optional local first bound in BOTH arms of an if/else hoists a
# `std::optional<T>` predecl at the chain head, and its narrowed reads deref
# through that binding. The deref is what makes the render right: the hoisted
# name is the optional itself, so a narrowed read must unwrap it before it
# reaches an int (or a len()) sink. The scalar inner and the owned-view inners
# (str / bytes) are both covered.
import asyncio
from tpy import int32
from typing import Iterator


class Box:
    tag: str

    def __init__(self, tag: str) -> None:
        self.tag = tag

    # method position: the same hoist inside a record method
    def pick(self, c: bool) -> int32:
        if c:
            n: int32 | None = 4  # tpyc: ok
        else:
            n = None
        if n is None:
            return 0
        return n + len(self.tag)


# free function: the scalar inner, read narrowed after the chain
def scalar_position(c: bool) -> int32:
    if c:
        n: int32 | None = 7  # tpyc: ok
    else:
        n = None
    if n is None:
        return 0
    return n


# free function: the owned-view inners
def view_position(c: bool) -> int32:
    if c:
        s: str | None = "abc"  # tpyc: ok
    else:
        s = None
    if c:
        b: bytes | None = b"xy"  # tpyc: ok
    else:
        b = None
    total = 0
    if s is not None:
        total += len(s)
    if b is not None:
        total += len(b)
    return total


# generator: the predecl sits in the resumable frame (the owned-VIEW inner
# rejects there, at res.local_storage -- a resumable rung, not this one)
def gen(c: bool) -> Iterator[int32]:
    if c:
        n: int32 | None = 3  # tpyc: ok
    else:
        n = None
    yield 1
    if n is not None:
        yield n


# async: the coroutine sibling
async def coro(c: bool) -> int32:
    if c:
        n: int32 | None = 5  # tpyc: ok
    else:
        n = None
    await asyncio.sleep(0)
    if n is None:
        return 0
    return n


# closure: the hoist is a local of the nested def
def closure_position(c: bool) -> int32:
    def inner() -> int32:
        if c:
            s: str | None = "wxyz"  # tpyc: ok
        else:
            s = None
        if s is None:
            return 0
        return len(s)

    return inner()


# try/finally: the try family shares the predecl classifier
def try_position(c: bool) -> int32:
    try:
        if c:
            s: str | None = "tt"  # tpyc: ok
        else:
            s = None
        if s is None:
            return 0
        return len(s)
    finally:
        print("try: cleanup")


def main() -> None:
    print("method:", Box("hi").pick(True), Box("hi").pick(False))
    print("scalar:", scalar_position(True), scalar_position(False))
    print("view:", view_position(True), view_position(False))
    print("gen:", end=" ")
    for v in gen(True):
        print(v, end=" ")
    print()
    print("async:", asyncio.run(coro(True)), asyncio.run(coro(False)))
    print("closure:", closure_position(True), closure_position(False))
    # bound first: the finally body writes to stdout, and a print argument
    # that prints interleaves ahead of the earlier arguments
    # (BUGS.md#subexpression-right-to-left-eval)
    t = try_position(True)
    print("try:", t)


main()
