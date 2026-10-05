# An empty list bound to an unannotated local takes its element from its first
# store or typed container, as if written in the literal; reads follow it.
import asyncio
from enum import Enum
from typing import Iterable, Iterator

from tpy import float32, int32, int64, error_return, ReturnException


class NotFound(Exception, ReturnException):
    pass


class Color(Enum):
    RED = 1
    GREEN = 2


class Guard:
    tag: int32

    def __init__(self, tag: int32) -> None:
        self.tag = tag

    def __enter__(self) -> int32:
        return self.tag

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


def wide() -> int64:
    return 1099511627776


def f32() -> float32:
    return 1.5


def big(v: list[int64]) -> None:
    v.append(7)


def maybe(v: list[int64] | None) -> None:
    if v is not None:
        v.append(9)


class Box:
    v: list[int64]

    def __init__(self) -> None:
        self.v = []


def total(v: Iterable[int64]) -> int64:
    t: int64 = 0
    for x in v:
        t += x
    return t


# free function -- a literal first store, then a wider typed store
def free_fn() -> None:
    ys = []  # tpyc: type(list[int64])
    ys.append(1)
    n = ys[0]  # tpyc: type(int64)
    ys.append(wide())  # tpyc: ok
    print("free:", n, sum(ys), ys)


class Holder:
    total: int64

    # constructor
    def __init__(self) -> None:
        ys = []  # tpyc: type(list[int64])
        ys.append(2)
        ys.append(wide())  # tpyc: ok
        self.total = ys[0] + ys[1]

    # method
    def run(self) -> None:
        ys = []  # tpyc: type(list[int64])
        ys.insert(0, 3)
        ys.append(wide())  # tpyc: ok
        print("method:", ys[0], ys)


# generator
def gen_pos() -> Iterator[int64]:
    ys = []  # tpyc: type(list[int64])
    ys.append(4)
    ys.append(wide())  # tpyc: ok
    yield ys[0]
    yield ys[1]


# async
async def async_pos() -> int64:
    await asyncio.sleep(0)
    ys = []  # tpyc: type(list[int64])
    ys.append(5)
    ys.append(wide())  # tpyc: ok
    return ys[0] + ys[1]


# comprehension body -- reads the widened list
def comp_pos() -> None:
    ys = []  # tpyc: type(list[int64])
    ys.append(6)
    ys.append(wide())  # tpyc: ok
    print("comp:", [v + 1 for v in ys])


# closure -- the enclosing function seeds and widens the list; the nested
# function stores and reads through the capture, which decides the element
def closure_pos() -> None:
    ys = []  # tpyc: type(list[int64])
    ys.append(7)
    ys.append(wide())  # tpyc: ok

    def inner() -> int64:
        ys.append(8)  # tpyc: ok
        return ys[1] - ys[0]

    n = inner()
    print("closure:", n, ys)

    # the empty list is the nested function's own local
    def own() -> None:
        zs = []  # tpyc: type(list[int64])
        zs.append(7)
        zs.append(wide())  # tpyc: ok
        print("closure:", zs[0], zs)

    own()


# with body
def with_pos() -> None:
    with Guard(8) as tag:
        ys = []  # tpyc: type(list[int32])
        ys.append(tag)
        print("with:", ys)
    zs = []  # tpyc: type(list[int64])
    with Guard(9):
        zs.append(9)
        zs.append(wide())  # tpyc: ok
    print("with:", zs[0], zs)


# try / finally
def try_pos() -> None:
    ys = []  # tpyc: type(list[int64])
    try:
        ys.append(10)
        ys.append(wide())  # tpyc: ok
    finally:
        print("try:", ys[0], ys)


# @error_return body
@error_return(NotFound)
def er_pos(ok: bool) -> int64:
    ys = []  # tpyc: type(list[int64])
    ys.append(11)
    if not ok:
        raise NotFound
    ys.append(wide())  # tpyc: ok
    return ys[0] + ys[1]


# match arm
def match_pos(c: Color) -> None:
    match c:
        case Color.RED:
            ys = []  # tpyc: type(list[int64])
            ys.append(12)
            ys.append(wide())  # tpyc: ok
            print("match:", ys[0], ys)
        case _:
            print("match: other")


# alias before the seed: seeded through the alias, read through the first
# name; the list is mutated after the alias, so a copy would print differently
def alias_seed_through_alias() -> None:
    ys = []  # tpyc: type(list[int64])
    zs = ys
    zs.append(wide())  # tpyc: ok
    zs.append(13)
    k = ys[0]  # tpyc: type(int64)
    print("alias1:", k, ys, zs)


# alias before the seed, the other way round
def alias_seed_through_first() -> None:
    ys = []  # tpyc: type(list[int64])
    zs = ys
    ys.append(14)
    ys.append(wide())  # tpyc: ok
    print("alias2:", zs[1], ys, zs)


# a first store in a loop body, read after the loop
def loop_pos() -> None:
    out = []  # tpyc: type(list[int64])
    for i in range(3):
        out.append(wide())  # tpyc: ok
    m = out[0]  # tpyc: type(int64)
    print("loop:", m, len(out))


# extend and += store every value: a literal, a typed list, a cell list
def extend_pos(typed: list[int64]) -> None:
    vs = []  # tpyc: type(list[int64])
    vs.extend([1, 2])
    vs += [3]
    vs.append(wide())  # tpyc: ok
    print("extend_lit:", vs)
    ws = []  # tpyc: type(list[int64])
    ws.extend(typed)
    ws += [15]
    print("extend_typed:", ws)
    # A list whose element a cell decides is settled by the call and
    # stores its element, a typed value: `xs` holds int32 elements.
    src = [16]  # tpyc: type(list[int32])
    xs = []  # tpyc: type(list[int32])
    xs.extend(src)
    xs += src  # tpyc: ok
    src.append(22)
    print("extend_cell:", xs, src)


# rebinding the empty list to a literal seeds it
def rebind_pos() -> None:
    xs = []  # tpyc: type(list[int64])
    xs = [1, 2]  # tpyc: type(list[int64])
    xs.append(wide())  # tpyc: ok
    print("rebind:", xs[0], xs)


# list() is an empty list
def ctor_pos() -> None:
    ys = list()  # tpyc: type(list[int64])
    ys.append(17)
    ys.append(wide())  # tpyc: ok
    print("ctor:", ys)


# a subscript store is a store
def setitem_pos() -> None:
    ys = []  # tpyc: type(list[int64])
    ys.append(18)
    ys[0] = wide()  # tpyc: ok
    print("setitem:", ys[0], ys)


# the float family
def float_pos() -> None:
    fs = []  # tpyc: type(list[float])
    fs.append(0.25)
    fs.append(f32())  # tpyc: ok
    x = fs[0]  # tpyc: type(float)
    print("float:", x, fs)


# the float family, a typed float32 store first: the element is float32, and a
# float literal stored later is rounded to it (0.5 is exact in float32)
def float32_pos() -> None:
    fs = []  # tpyc: type(list[float32])
    fs.append(f32())
    fs.append(0.5)  # tpyc: ok
    print("float32:", fs)


# a parameter widens a literal-seeded list, and a read after it is int64
def param_widens() -> None:
    ys = []  # tpyc: type(list[int64])
    ys.append(1)
    big(ys)
    n = ys[1]  # tpyc: type(int64)
    print("param:", n, ys)


# an augmented store into an element widens a literal-seeded list
def augitem_pos() -> None:
    ys = []  # tpyc: type(list[int64])
    ys.append(1)
    ys[0] += wide()  # tpyc: ok
    n = ys[0]  # tpyc: type(int64)
    print("augitem:", n, ys)


# a declared yield type first, then a store; the consumer mutates the list
def yield_first() -> Iterator[list[int64]]:
    ys = []  # tpyc: type(list[int64])
    yield ys  # tpyc: ok
    ys.append(wide())  # tpyc: ok
    yield ys


# a typed field first, then a store; the field store copies the list
# (warned, named at the field's element), so only the local is read after.
def field_first() -> None:
    b = Box()
    ys = []  # tpyc: type(list[int64])
    b.v = ys  # tpyc: warning(/copies list\[int64\] into field/)
    ys.append(wide())  # tpyc: ok
    print("field:", ys)


# an Optional member first, then a store; the callee mutates the list
def optional_first() -> None:
    ys = []  # tpyc: type(list[int64])
    maybe(ys)  # tpyc: ok
    ys.append(wide())  # tpyc: ok
    print("optional:", ys)


# len() needs no element type: the store after it seeds the list
def len_first() -> None:
    ys = []  # tpyc: type(list[int64])
    n = len(ys)
    ys.append(wide())  # tpyc: ok
    print("len:", n, ys)


# a typed container first: the parameter decides the element, and the list
# it handed over is mutated by the callee
def context_first() -> None:
    ys = []  # tpyc: type(list[int64])
    big(ys)
    ys.append(19)  # tpyc: ok
    print("context:", ys)


# a view parameter as the first context
def view_first() -> None:
    ys = []  # tpyc: type(list[int64])
    print("view:", total(ys))
    ys.append(wide())  # tpyc: ok
    print("view:", total(ys), ys)


def main() -> None:
    free_fn()
    h = Holder()
    print("ctor_field:", h.total)
    h.run()
    print("gen:", list(gen_pos()))
    print("async:", asyncio.run(async_pos()))
    comp_pos()
    closure_pos()
    with_pos()
    try_pos()
    try:
        print("error_return:", er_pos(True))
        er_pos(False)
    except NotFound:
        print("error_return: raised")
    match_pos(Color.RED)
    alias_seed_through_alias()
    alias_seed_through_first()
    loop_pos()
    extend_pos([20, 21])
    rebind_pos()
    ctor_pos()
    setitem_pos()
    float_pos()
    float32_pos()
    param_widens()
    augitem_pos()
    for g in yield_first():
        g.append(1)
        print("yield:", g)
    field_first()
    optional_first()
    len_first()
    context_first()
    view_first()


main()
