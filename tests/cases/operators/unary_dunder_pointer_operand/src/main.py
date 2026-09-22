# A unary dunder (`-`, `+`, `~`) reads its operand exactly as a BINARY one
# does, so a pointer-form operand (narrowed Optional, escape-hoisted local)
# derefs instead of rendering the bare pointer, and a record FIELD operand
# reads bare.
# `@nocopy` on the operand type so a silent copy at any of these reads is a
# compile error rather than a passing test.
import asyncio
from typing import Iterator, Optional

from tpy import int32, nocopy


class Tag:
    k: int32

    def __init__(self, k: int32) -> None:
        self.k = k


@nocopy
class Cell:
    n: int32
    tag: Tag

    def __init__(self, n: int32) -> None:
        self.n = n
        self.tag = Tag(n)

    def __neg__(self) -> int32:
        return -self.n

    def __pos__(self) -> int32:
        return self.n

    def __invert__(self) -> int32:
        return ~self.n

    def __add__(self, k: int32) -> int32:
        return self.n + k


class Holder:
    c: Cell

    def __init__(self, n: int32) -> None:
        self.c = Cell(n)


# free function -- narrowed Optional PARAM: `o` binds as a pointer, and the
# binop twin on the same line already derefs.
def opt_param(o: Optional[Cell]) -> int32:
    if o is not None:
        return (o + 1) + (-o) + (+o) + (~o)  # tpyc: ok
    return 0


# free function -- narrowed Optional LOCAL, all three unary operators.
def opt_local(flag: bool) -> int32:
    maybe: Optional[Cell] = None
    if flag:
        maybe = Cell(6)
    if maybe is not None:
        return (-maybe) + (+maybe) + (~maybe)  # tpyc: ok
    return 0


# free function -- an escape-hoisted local, which binds as a pointer too.
def escape_hoisted() -> int32:
    keep = Tag(0)
    acc = 0
    for i in range(3):
        c = Cell(i)
        acc += -c  # tpyc: ok
        # the hoisted slot outlives `c`, so it cannot hold a borrow of its field
        keep = c.tag  # tpyc: warning(/will not keep the object it was given/)
    return acc + keep.k


# free function -- a record FIELD operand reads bare, like the binop's.
def field_operand(h: Holder) -> int32:
    return -h.c  # tpyc: ok


# free function -- a for-each borrow.
def foreach(cells: list[Cell]) -> int32:
    acc = 0
    for c in cells:
        acc += -c  # tpyc: ok
    return acc


# free function -- a ptr-variant union alternative narrowed to the operand.
def union_alt(u: Cell | Holder) -> int32:
    if isinstance(u, Cell):
        return -u  # tpyc: ok
    return 0


class Reader:
    v: int32

    # constructor -- a narrowed Optional param operand.
    def __init__(self, o: Optional[Cell]) -> None:
        self.v = 0
        if o is not None:
            self.v = -o  # tpyc: ok

    # method -- a record field operand.
    def read(self, h: Holder) -> int32:
        return ~h.c  # tpyc: ok


# generator -- the frame's own narrowed Optional param.
def gen(o: Optional[Cell]) -> Iterator[int32]:
    if o is not None:
        yield -o  # tpyc: ok
    yield 0


async def coro(h: Holder) -> int32:
    # async -- a record field operand inside a resumable frame.
    return -h.c  # tpyc: ok


# module level -- a top-level statement over a record field operand.
mod_h = Holder(7)
mod_read = -mod_h.c  # tpyc: ok


def main() -> None:
    c = Cell(3)
    print("optparam", opt_param(c))
    print("optlocal", opt_local(True), opt_local(False))
    print("escapehoist", escape_hoisted())
    h = Holder(4)
    print("field", field_operand(h))
    cells = [Cell(1), Cell(2)]
    print("foreach", foreach(cells))
    print("union", union_alt(c))
    print("ctor", Reader(c).v)
    print("method", Reader(None).read(h))
    for v in gen(c):
        print("gen", v)
    print("async", asyncio.run(coro(h)))
    print("module", mod_read)
    # Mutating through the same object after the borrow reads proves the
    # operand was read through, not snapshotted.
    h.c.n = 40
    print("field2", field_operand(h))


main()
