# The numeric leaves of an unannotated list's element -- tuple members, the
# elements of nested rows -- are decided as a scalar element is; reads follow.
import asyncio
from enum import Enum
from typing import Iterator

from tpy import copy, float32, int32, int64, error_return, ReturnException, Own


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
    return 0.25


def pairs64(v: list[tuple[int64, int32]]) -> None:
    v.append((5, 6))


def rows64(v: list[list[int64]]) -> None:
    v[0].append(9)


def row64(v: list[int64]) -> None:
    v.append(8)


# free function -- a tuple member and a row element widen
def free_fn() -> None:
    xs = [(1, 2)]  # tpyc: type(list[tuple[int64, int32]])
    xs.append((wide(), 3))  # tpyc: ok
    g = [[1, 2], [3]]  # tpyc: type(Array[list[int64], 2])
    g[0].append(wide())  # tpyc: ok
    print("free:", xs[0][0], g[1][0], xs, g)


class Holder:
    total: int64

    # constructor
    def __init__(self) -> None:
        xs = [(1, 2)]  # tpyc: type(list[tuple[int64, int32]])
        xs.append((wide(), 3))  # tpyc: ok
        g = [[1], [2]]  # tpyc: type(Array[list[int64], 2])
        g[1].append(wide())  # tpyc: ok
        self.total = xs[0][0] + g[0][0]

    # method
    def run(self) -> None:
        xs = [(1, 2)]  # tpyc: type(list[tuple[int64, int32]])
        xs.append((wide(), 3))  # tpyc: ok
        g = [[1], [2]]  # tpyc: type(list[Array[int64, 1]])
        g.append([wide()])  # tpyc: ok
        print("method:", xs[1], g)


# generator
def gen_pos() -> Iterator[int64]:
    xs = [(1, 2)]  # tpyc: type(list[tuple[int64, int32]])
    xs.append((wide(), 3))  # tpyc: ok
    g = [[4], [5]]  # tpyc: type(Array[list[int64], 2])
    g[0].append(wide())  # tpyc: ok
    yield xs[1][0]
    yield g[0][1]


# async
async def async_pos() -> int64:
    await asyncio.sleep(0)
    xs = [(1, 2)]  # tpyc: type(list[tuple[int64, int32]])
    xs.append((wide(), 3))  # tpyc: ok
    g = [[6], [7]]  # tpyc: type(Array[list[int64], 2])
    g[1].append(wide())  # tpyc: ok
    return xs[1][0] + g[1][1]


# comprehension body -- reads the widened lists
def comp_pos() -> None:
    xs = [(1, 2)]  # tpyc: type(list[tuple[int64, int32]])
    xs.append((wide(), 3))  # tpyc: ok
    g = [[8], [9]]  # tpyc: type(Array[list[int64], 2])
    g[0].append(wide())  # tpyc: ok
    print("comp:", [a + 1 for a, b in xs], [len(r) for r in g])


# closure -- the nested function's own lists
def closure_pos() -> None:
    def inner() -> int64:
        xs = [(1, 2)]  # tpyc: type(list[tuple[int64, int32]])
        xs.append((wide(), 3))  # tpyc: ok
        g = [[10], [11]]  # tpyc: type(Array[list[int64], 2])
        g[0].append(wide())  # tpyc: ok
        return xs[1][0] - g[0][1]

    print("closure:", inner())


# with body
def with_pos() -> None:
    with Guard(12) as tag:
        xs = [(1, 2)]  # tpyc: type(list[tuple[int64, int32]])
        xs.append((wide(), tag))  # tpyc: ok
        g = [[13], [14]]  # tpyc: type(list[list[int64]])
        g[1].append(wide())  # tpyc: ok
        print("with:", xs[1], g)


# try / finally
def try_pos() -> None:
    xs = [(1, 2)]  # tpyc: type(list[tuple[int64, int32]])
    g = [[15], [16]]  # tpyc: type(Array[list[int64], 2])
    try:
        xs.append((wide(), 3))  # tpyc: ok
        g[0].append(wide())  # tpyc: ok
    finally:
        print("try:", xs[1][0], g)


# @error_return body
@error_return(NotFound)
def er_pos(ok: bool) -> int64:
    xs = [(1, 2)]  # tpyc: type(list[tuple[int64, int32]])
    g = [[17], [18]]  # tpyc: type(Array[list[int64], 2])
    if not ok:
        raise NotFound
    xs.append((wide(), 3))  # tpyc: ok
    g[1].append(wide())  # tpyc: ok
    return xs[1][0] + g[1][1]


# match arm
def match_pos(c: Color) -> None:
    match c:
        case Color.RED:
            xs = [(1, 2)]  # tpyc: type(list[tuple[int64, int32]])
            xs.append((wide(), 3))  # tpyc: ok
            g = [[19], [20]]  # tpyc: type(Array[list[int64], 2])
            g[0].append(wide())  # tpyc: ok
            print("match:", xs[1], g)
        case _:
            print("match: other")


# a member read and an unpack after the widening read the widened member;
# a member read before it follows it too
def reads_pos() -> None:
    xs = [(1, 2)]  # tpyc: type(list[tuple[int64, int32]])
    early = xs[0][0]  # tpyc: type(int64)
    xs.append((wide(), 3))  # tpyc: ok
    n = xs[1][0]  # tpyc: type(int64)
    a, b = xs[1]
    print("reads:", early, n, a, b)


# iteration decides the element from what the list holds so far: after the
# widening it reads the widened members
def loop_pos() -> None:
    xs = [(1, 2)]  # tpyc: type(list[tuple[int64, int32]])
    xs.append((wide(), 3))  # tpyc: ok
    for a, b in xs:
        print("loop:", a, b)
    g = [[1], [2, 3]]  # tpyc: type(Array[list[int64], 2])
    g[0].append(wide())  # tpyc: ok
    for row in g:
        print("loop:", row)


# a typed container decides each leaf, at both depths; the callee's store
# is visible, and a later store fits
def context_pos() -> None:
    xs = [(1, 2)]  # tpyc: type(list[tuple[int64, int32]])
    pairs64(xs)  # tpyc: ok
    xs.append((7, 3))  # tpyc: ok
    g = [[1, 2], [3]]  # tpyc: type(list[list[int64]])
    rows64(g)  # tpyc: ok
    row64(g[1])  # tpyc: ok
    g[1].append(4)  # tpyc: ok
    print("context:", xs, g)


# a non-numeric member keeps its type; only the numeric one widens
def mixed_member_pos() -> None:
    ms = [(1, "a")]  # tpyc: type(list[tuple[int64, str]])
    ms.append((wide(), "b"))  # tpyc: ok
    print("mixed:", ms[1][0], ms[1][1], ms)


# three deep: the innermost element widens for every row
def three_deep_pos() -> None:
    gg = [[[1]], [[2]]]  # tpyc: type(Array[Array[list[int64], 1], 2])
    gg[0][0].append(wide())  # tpyc: ok
    print("deep:", gg[0][0][1], gg)


# a comprehension of rows: one row record, every row shares it
def comp_rows_pos() -> None:
    g = [[0] * 3 for _ in range(2)]  # tpyc: type(Array[list[int64], 2])
    g[0].append(wide())  # tpyc: ok
    print("comp_rows:", g)


# a name for a row is that row: written after the widening, printed through
# the outer list
def row_alias_pos() -> None:
    g = [[1, 2], [3]]  # tpyc: type(Array[list[int64], 2])
    row = g[0]  # tpyc: type(list[int64])
    g[1].append(wide())  # tpyc: ok
    row.append(7)
    print("row_alias:", g)


# a list stored as a row holds what the rows do: the source's widening
# widens the rows; the copy is explicit (copy() decides the source, as any
# generic call does), and the source written afterwards is not the row
def stored_row_pos() -> None:
    g = [[1, 2]]  # tpyc: type(list[list[int64]])
    other = [5]  # tpyc: type(list[int64])
    other.append(wide())  # tpyc: ok
    g.append(copy(other))  # tpyc: ok
    other.append(7)  # tpyc: ok
    print("stored_row:", g, other)


# a plain tuple literal appended, and a tuple stored through a subscript
def store_shapes_pos() -> None:
    xs = [(1, 2)]  # tpyc: type(list[tuple[int64, int32]])
    xs.append((3, 4))  # tpyc: ok
    xs[0] = (wide(), 5)  # tpyc: ok
    print("stores:", xs)


# an empty list seeded by a tuple, and one seeded by a row
def empty_seed_pos() -> None:
    e = []  # tpyc: type(list[tuple[int64, int32]])
    e.append((1, 2))
    e.append((wide(), 3))  # tpyc: ok
    e2 = []  # tpyc: type(list[list[int64]])
    e2.append([1])
    e2[0].append(wide())  # tpyc: ok
    print("empty_seed:", e, e2)


# extend and += with a list of tuples store each tuple
def extend_pos() -> None:
    xs = [(1, 2)]  # tpyc: type(list[tuple[int64, int32]])
    xs.extend([(wide(), 3), (4, 5)])  # tpyc: ok
    xs += [(6, 7)]  # tpyc: ok
    print("extend:", xs)


# a list appended as a row is copied where CPython aliases it: the
# documented copy-into-container rule, so the warning is right and copy()
# silences it. The source stays linked to the row element: its widening
# afterwards widens the rows' type, not the copy
def warned_copy_pos() -> None:
    xs = [[1, 2]]  # tpyc: type(list[list[int64]])
    row = [3]  # tpyc: type(list[int64])
    xs.append(row)  # tpyc: warning(/copies list\[int64\] into owned storage/)
    print("warned_copy:", len(xs[1]))
    row.append(wide())  # tpyc: ok
    print("warned_copy:", row)



# a list stored as a tuple member is copied (the documented container-insert
# divergence; copy() is the hatch): the warning names the member, and the
# tuple is printed before the source is written, where both runtimes agree
def tuple_copy_pos() -> None:
    zs = [5]  # tpyc: type(list[int32])
    xs = [(1, zs)]  # tpyc: warning(/copies list\[int32\] into owned storage \(tuple element 1\)/) type(Array[tuple[int32, list[int32]], 1])
    print("tuple_copy:", xs)
    zs.append(6)
    print("tuple_copy:", zs)


# a local rebound to a row of the list its literal went into follows the
# rows' list type
def row_rebind_pos() -> None:
    row = [1]  # tpyc: type(list[int32])
    row.append(2)
    g = [row]  # tpyc: warning(/copies list\[int32\] into owned storage/) type(Array[list[int32], 1])
    row = g[0]  # tpyc: type(list[int32])
    print("row_rebind:", g, len(row), row[0])


# += with a list of rows stores each row: the rows' list types resolve after
# the store
def row_iadd_pos() -> None:
    g = [[1]]  # tpyc: type(list[Array[int64, 1]])
    g += [[wide()]]  # tpyc: ok
    print("row_iadd:", g)

# a return slot is a typed container: it decides each leaf
def return_ctx_pos() -> Own[list[tuple[int64, int32]]]:
    xs = [(1, 2)]  # tpyc: type(list[tuple[int64, int32]])
    xs.append((3, 4))  # tpyc: ok
    return xs


# an empty row holds what its siblings do
def empty_row_pos() -> None:
    g = [[], [2]]  # tpyc: type(Array[list[int64], 2])
    g[0].append(wide())  # tpyc: ok
    print("empty_row:", g)


# a name bound to a row widens the row element of every row through it
def alias_widens_pos() -> None:
    g = [[1, 2], [3]]  # tpyc: type(Array[list[int64], 2])
    row = g[0]  # tpyc: type(list[int64])
    row.append(wide())  # tpyc: ok
    print("alias_widens:", g[0][2])


# a tuple read compares, and is popped, at the widened members
def tuple_sinks_pos() -> None:
    xs = [(1, 2)]  # tpyc: type(list[tuple[int64, int32]])
    xs.append((wide(), 3))  # tpyc: ok
    print("tuple_sinks:", xs[1] == (wide(), 3), (1, 2) in xs)
    r = xs.pop()  # tpyc: type(tuple[int64, int32])
    print("tuple_sinks:", r, xs)


# a float32 member takes an exactly representable literal
def float32_member_pos() -> None:
    fs = [(f32(), 1)]  # tpyc: type(list[tuple[float32, int32]])
    fs.append((0.5, 2))  # tpyc: ok
    print("float32_member:", fs)


# a row stored through a subscript is a store into the row element, as an
# append is: a literal row stores its values, a list moved in links, an
# empty list takes the row element, a typed list decides it
def subscript_row_pos() -> None:
    g = [[1], [2]]  # tpyc: type(Array[list[int64], 2])
    g[0] = [wide(), 3]  # tpyc: ok
    m = [[1], [2]]  # tpyc: type(Array[list[int64], 2])
    other = [5]  # tpyc: type(list[int64])
    other.append(wide())  # tpyc: ok
    m[1] = other  # tpyc: ok
    k = [[1], [2]]  # tpyc: type(Array[list[int64], 2])
    k[0] = []  # tpyc: ok
    k[0].append(wide())  # tpyc: ok
    r: list[int64] = [4]
    t = [[1], [2]]  # tpyc: type(Array[list[int64], 2])
    t[1] = r  # tpyc: ok
    print("subscript_row:", g, m, k, t)


def main() -> None:
    free_fn()
    h = Holder()
    print("ctor:", h.total)
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
    reads_pos()
    loop_pos()
    context_pos()
    mixed_member_pos()
    three_deep_pos()
    comp_rows_pos()
    row_alias_pos()
    stored_row_pos()
    store_shapes_pos()
    empty_seed_pos()
    extend_pos()
    warned_copy_pos()
    tuple_copy_pos()
    row_rebind_pos()
    row_iadd_pos()
    print("return_ctx:", return_ctx_pos())
    empty_row_pos()
    alias_widens_pos()
    tuple_sinks_pos()
    float32_member_pos()
    subscript_row_pos()


main()
