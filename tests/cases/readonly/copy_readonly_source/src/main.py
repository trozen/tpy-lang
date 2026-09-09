# copy() accepts a readonly source: copying never writes its source, so the
# result is an owned MUTABLE value that a readonly payload can reach a
# mutable/owning slot with. One section per position; each mutates the copy and
# shows the readonly source unchanged.
from __future__ import annotations
import asyncio
from typing import Iterator
from tpy import Int32, Own, readonly, auto_readonly, copy, error_return, ReturnException


class Cell:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


def bump(c: Own[Cell]) -> Own[Cell]:
    c.n = c.n + 100
    return c


class Holder:
    cell: Cell

    def __init__(self, c: Own[Cell]) -> None:
        self.cell = c

    @auto_readonly
    def get(self) -> auto_readonly[Cell]:
        return self.cell

    # method: a readonly field read reaches the Own[Cell] slot via copy().
    @readonly
    def sec_method(self) -> None:
        d = bump(copy(self.cell))  # tpyc: ok
        print("method:", self.cell.n, d.n)

    # method: the source is an auto_readonly BORROW RETURN, not a field read.
    @readonly
    def sec_borrow_ret(self) -> None:
        d = bump(copy(self.get()))  # tpyc: ok
        print("borrow-ret:", self.get().n, d.n)


class GHolder[T]:
    val: T

    def __init__(self, v: Own[T]) -> None:
        self.val = v

    @auto_readonly
    def get(self) -> auto_readonly[T]:
        return self.val

    # generic body: the Box.clone shape -- copy() over a readonly[T] borrow
    # return into an Own[T] constructor slot.
    @readonly
    def cloned(self) -> Own[GHolder[T]]:
        return GHolder(copy(self.get()))  # tpyc: ok


# free function: a readonly param reaches an Own[Cell] param.
def sec_free(c: readonly[Cell]) -> None:
    d = bump(copy(c))  # tpyc: ok
    print("free:", c.n, d.n)


# constructor: the copy is the ctor's Own[Cell] argument.
def sec_ctor(c: readonly[Cell]) -> None:
    h = Holder(copy(c))  # tpyc: ok
    h.cell.n = 7
    print("ctor:", c.n, h.cell.n)


def sec_generic(c: readonly[Cell]) -> None:
    g = GHolder[Cell](Cell(c.n))
    g2 = g.cloned()
    g2.get().n = 7
    print("generic:", g.get().n, g2.get().n)


# generator: the copy happens inside a resumable frame.
def gen_copies(c: readonly[Cell]) -> Iterator[Int32]:
    d = bump(copy(c))  # tpyc: ok
    yield d.n
    yield c.n


def sec_generator(c: readonly[Cell]) -> None:
    out: list[Int32] = []
    for n in gen_copies(c):
        out.append(n)
    print("generator:", out[1], out[0])


# async: the copy happens inside a coroutine frame.
async def copy_in_task(c: readonly[Cell]) -> Int32:
    d = bump(copy(c))  # tpyc: ok
    return d.n


def sec_async(c: readonly[Cell]) -> None:
    print("async:", c.n, asyncio.run(copy_in_task(c)))


# comprehension: the loop variable over a readonly list is readonly.
def sec_comprehension(cs: readonly[list[Cell]]) -> None:
    ns = [bump(copy(c)).n for c in cs]  # tpyc: ok
    print("comprehension:", cs[0].n, ns[0])


# closure: the copy is inside a nested function.
def sec_closure(c: readonly[Cell]) -> None:
    def inner() -> Int32:
        return bump(copy(c)).n  # tpyc: ok
    print("closure:", c.n, inner())


class Guard:
    def __enter__(self) -> None:
        pass

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


# context-manager body.
def sec_with(c: readonly[Cell]) -> None:
    with Guard():
        d = bump(copy(c))  # tpyc: ok
        print("with:", c.n, d.n)


# try/finally body.
def sec_try_finally(c: readonly[Cell]) -> None:
    try:
        d = bump(copy(c))  # tpyc: ok
        print("try-finally:", c.n, d.n)
    finally:
        pass


class Missing(Exception, ReturnException):
    pass


# @error_return body.
@error_return(Missing)
def ret_copy(c: readonly[Cell]) -> Int32:
    d = bump(copy(c))  # tpyc: ok
    return d.n


def sec_error_return(c: readonly[Cell]) -> None:
    try:
        print("error-return:", c.n, ret_copy(c))
    except Missing:
        print("error-return: missing")


# match arm.
def sec_match(c: readonly[Cell], k: Int32) -> None:
    match k:
        case 1:
            d = bump(copy(c))  # tpyc: ok
            print("match:", c.n, d.n)
        case _:
            print("match: none")


def peek(h: readonly[Holder]) -> readonly[Cell]:
    return h.cell


# module-level statement: globals use pointer slots, so the copy is spelled
# against a different variable model than the function sections above.
TOP_SRC = Holder(Cell(1))
TOP = bump(copy(peek(TOP_SRC)))  # tpyc: ok


def main() -> None:
    print("module-level:", TOP_SRC.cell.n, TOP.n)
    c = Cell(1)
    sec_free(c)
    Holder(Cell(1)).sec_method()
    Holder(Cell(1)).sec_borrow_ret()
    sec_ctor(c)
    sec_generic(c)
    sec_generator(c)
    sec_async(c)
    cs: list[Cell] = [Cell(1)]
    sec_comprehension(cs)
    sec_closure(c)
    sec_with(c)
    sec_try_finally(c)
    sec_error_return(c)
    sec_match(c, 1)


main()
