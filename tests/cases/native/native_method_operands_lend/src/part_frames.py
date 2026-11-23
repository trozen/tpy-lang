# A free stub's result lends from its operands across a suspension, as its
# TPy twin's does: held in a generator or a coroutine frame and written.
# A generic frame at its instantiation captures as its concrete twin's: a
# `str` is copied into the frame, a `StrView` is a borrow.
import asyncio
from typing import Iterator
from tpy import int32, StrView
from tpy.extern import native
from fixtures import Node


@native
def first_of(n: Node) -> Node: ...


@native
class Holder:
    @staticmethod
    @native("first")
    def pick(n: Node) -> Node: ...


def gen(n: Node) -> Iterator[int32]:
    # generator: the alias of `n` survives the yield
    r = first_of(n)  # tpyc: ok
    yield r.v
    r.v = 9
    yield n.v


def gen_static(n: Node) -> Iterator[int32]:
    # static method stub
    r = Holder.pick(n)  # tpyc: ok
    yield r.v
    r.v = 8
    yield n.v


async def held(n: Node) -> int32:
    # async: the alias survives the await
    r = first_of(n)  # tpyc: ok
    await asyncio.sleep(0)
    r.v = 7
    return n.v


def gen_generic[T](s: T) -> Iterator[int32]:
    yield 1


async def task_generic[T](v: T) -> int32:
    return 2


def generic_gen() -> None:
    s = "ab" * 3
    g = gen_generic(s)
    # generic generator at `str`: the frame owns its copy of `s`
    s += "!"  # tpyc: ok
    total = 0
    for v in g:
        total += v
    print("frame generic gen:", total, s)


async def generic_task() -> int32:
    t = "cd" * 3
    c = task_generic(StrView(t))
    # generic coroutine at `StrView`: the frame borrows `t`
    t += "!"  # tpyc: warning(/Mutation of 't' while borrowed/)
    return await c


def main() -> None:
    n = Node(1)
    print("frame gen:", [v for v in gen(n)])
    m = Node(2)
    print("frame static:", [v for v in gen_static(m)])
    print("frame async:", asyncio.run(held(Node(3))))
    generic_gen()
    print("frame generic task:", asyncio.run(generic_task()))
