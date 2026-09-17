# A function-local that SHADOWS a native global and is first bound inside a
# BRANCH. Python scopes a name the function assigns to the function, whatever
# the module binds, so the branch-first predecl must declare a LOCAL and every
# read after the block must be that local -- reading the extern instead would
# return whatever the C++ side holds. The extern is never read, so this program
# needs no C++ companion and runs under CPython too; the straight-line shadow
# is native_global_local_shadow. The nested-def position is NOT covered: a
# nested def's local named like a module global is emitted as a write to the
# global (BUGS.md#nested-local-shadows-module-global).
import asyncio
from tpy.extern import native_global
from tpy import int32
from typing import Iterator

frame_count: int32 = native_global("DG_FrameCount", binding="C")
score: int32 = native_global("engine::score")


def probe(n: int32) -> int32:
    if n < 0:
        raise ValueError("neg")
    return n


class Engine:
    base: int32

    def __init__(self, base: int32) -> None:
        self.base = base

    # method position
    def step(self, c: bool) -> int32:
        if c:  # tpyc: ok
            frame_count = 1
        else:
            frame_count = 2
        return frame_count + self.base


# free function: the branch-first shadow
def pick(c: bool) -> int32:
    if c:  # tpyc: ok
        frame_count = 1
    else:
        frame_count = 2
    return frame_count


# try body: the same shadow through the try/except hoist
def guarded(n: int32) -> int32:
    try:  # tpyc: ok
        score = probe(n)
    except ValueError:
        return -1
    return score


# generator: the predecl lives in the resumable frame
def gen(c: bool) -> Iterator[int32]:
    if c:  # tpyc: ok
        score = 3
    else:
        score = 4
    yield score
    yield score + 1


# match arm: the same shadow through the match hoist
def routed(n: int32) -> int32:
    match n:  # tpyc: ok
        case 1:
            score = 5
        case _:
            score = 6
    return score


# async: the coroutine sibling
async def coro(c: bool) -> int32:
    if c:  # tpyc: ok
        frame_count = 5
    else:
        frame_count = 6
    await asyncio.sleep(0)
    return frame_count


def main() -> None:
    print("free:", pick(True), pick(False))
    print("method:", Engine(10).step(True))
    print("try:", guarded(7), guarded(-1))
    print("gen:", end=" ")
    for v in gen(True):
        print(v, end=" ")
    print()
    print("match:", routed(1), routed(9))
    print("async:", asyncio.run(coro(True)))


main()
