# The binding loop inside an if / with / try / match block, read after the
# block, and the resumable (generator, async) sibling and block shapes.
from typing import Iterator
from tpy import int32
from fixtures import Flat, Pic, CM
import asyncio


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


def main() -> None:
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
