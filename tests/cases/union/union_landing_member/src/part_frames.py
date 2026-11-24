# Values that reach a union boundary through a rewritten or lifted
# expression: a decided ternary's live arm, an inline `await` argument in a
# coroutine body (bare and through a hoisted call), a generator factory's
# argument held in a frame temp, an `@error_return` call's unwrapped
# value returned from a plain function, and a coroutine's finally-deferred
# return of a subclass object.
import asyncio
from typing import Iterator, Literal
from tpy import Own, ReturnException, error_return, int64, uint32, uint64
from part_records import Base, Leaf, Mid
from part_finally import FBase, FSub


# decided ternary: the live arm `c` is the declaration's value and lands
# in int64, as `v: int64 | uint64 = c` does
def decided(c: uint32) -> None:
    mode: Literal["a"] = "a"
    v: int64 | uint64 = c if mode == "a" else uint64(9)  # tpyc: ok
    print("frames-decided", v)


def show(u: int64 | uint64) -> None:
    print("frames-await-arg", u)


async def take(u: int64 | uint64) -> int:
    await asyncio.sleep(0)
    show(u)
    return 1


def pick(c: uint32) -> uint32:
    return c


# async: an inline await argument lands at the parameter, also when the
# argument call is hoisted into the frame
async def body(c: uint32) -> None:
    r = await take(c)  # tpyc: ok
    s = await take(pick(c))  # tpyc: ok
    print("frames-await", r, s)


# async finally: the frame reads the captured object after the chain and
# hands over its base part, as the sync return does
async def deferred() -> Own[FBase]:
    s = FSub(5)
    await asyncio.sleep(0)
    try:
        return s  # tpyc: warning(/narrows 'FSub'/)
    finally:
        s.n += 10


async def run_deferred() -> None:
    r = await deferred()
    print("frames-async-finally", r.n)


def gen_u(u: Base | Mid) -> Iterator[int]:
    if isinstance(u, Mid):
        yield u.n + 100
    else:
        yield u.n


# generator factory: the fresh Leaf is held in a frame temp and lent as Mid
def frame_temp() -> None:
    for x in gen_u(Leaf(6)):  # tpyc: ok
        print("frames-temp", x)


class Failed(Exception, ReturnException):
    pass


@error_return(Failed)
def checked(n: uint32) -> uint32:
    if n == 0:
        raise Failed()
    return n


# @error_return call: a plain function unwraps the result, which lands in
# int64 as `return c` does (an @error_return caller passes the result
# through whole, where the callee's type is the member it lands in)
def unwrapped(n: uint32) -> int64 | str:
    try:
        return checked(n)  # tpyc: ok
    except Failed:
        return "failed"


def main() -> None:
    v = unwrapped(5)
    print("frames-error-return", v, isinstance(v, str))
    decided(3)
    asyncio.run(body(4))
    asyncio.run(run_deferred())
    frame_temp()
