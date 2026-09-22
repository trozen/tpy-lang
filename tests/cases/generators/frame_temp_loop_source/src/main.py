# A `for` inside a generator or `async def` whose body does NOT suspend keeps its
# block-local iterators, and the frame keeps a FRESH iterable exactly when the
# body binds something out of an element into storage that borrows -- so such a
# name stays valid across a suspension. A loop that binds nothing out keeps the
# holder in its own block, where CPython drops it.
import asyncio

from tpy import Own, Ptr, int32
from typing import Iterator


class Inner:
    w: int32

    def __init__(self, w: int32) -> None:
        self.w = w


class Cell:
    v: int32
    inner: Inner

    def __init__(self, v: int32) -> None:
        self.v = v
        self.inner = Inner(v * 10)


def make_cells() -> Own[list[Cell]]:
    return [Cell(11), Cell(22)]


def make_values() -> Own[list[int32]]:
    return [11, 22]


def make_name() -> Own[str]:
    return "ab"


class Holder:
    c: Ptr[Cell]

    def __init__(self, c: Ptr[Cell]) -> None:
        self.c = c


class Feed:
    cells: list[Cell]
    i: int32

    def __init__(self) -> None:
        self.cells = [Cell(11), Cell(22)]
        self.i = 0

    def __iter__(self) -> "Feed":
        return self

    def __next__(self) -> Cell:
        if self.i >= len(self.cells):
            raise StopIteration()
        c = self.cells[self.i]
        self.i += 1
        return c


def make_feed() -> Own[Feed]:
    return Feed()


def counted() -> Iterator[int32]:
    try:
        yield 1
        yield 2
        yield 3
    finally:
        print("counted finally")


def pair_of(c: Cell) -> tuple[Cell, int32]:
    return (c, 7)


def churn() -> int32:
    # Disturbs the storage a dangling read would land in.
    junk = [Cell(i) for i in range(60)]
    return len(junk)


# free generator, plain binding: `r` and `alias` name the same element, so a
# holder copied per state block (or freed with one) shows up in `alias.v`
def gen_plain() -> Iterator[str]:
    for x in make_cells():  # tpyc: ok
        r = x
        alias = x
        break
    else:
        raise ValueError("empty")
    yield "gen_plain sep"
    n = churn()
    r.v = 99
    yield f"gen_plain {alias.v} {n}"


# tuple-unpack binding beside the scalar the same unpack produces
def gen_unpack() -> Iterator[str]:
    for x in make_cells():  # tpyc: ok
        r, k = pair_of(x)
        alias = x
        break
    else:
        raise ValueError("empty")
    yield "gen_unpack sep"
    n = churn()
    r.v = 98
    yield f"gen_unpack {alias.v} {k} {n}"


# a reference-typed FIELD of the element
def gen_field() -> Iterator[str]:
    for x in make_cells():  # tpyc: ok
        r = x.inner
        alias = x
        break
    else:
        raise ValueError("empty")
    yield "gen_field sep"
    n = churn()
    r.w = 97
    yield f"gen_field {alias.inner.w} {n}"


# the same name bound in BOTH arms of an if
def gen_two_arms(pick: bool) -> Iterator[str]:
    for x in make_cells():  # tpyc: ok
        if pick:
            r = x
        else:
            r = x
        alias = x
        break
    else:
        raise ValueError("empty")
    yield "gen_two_arms sep"
    n = churn()
    r.v = 96
    yield f"gen_two_arms {alias.v} {n}"


# nested loops: the OUTER element is captured from inside the inner one
def gen_nested() -> Iterator[str]:
    for x in make_cells():  # tpyc: ok
        for y in make_cells():  # tpyc: ok
            r = x
            q = y
            break
        else:
            raise ValueError("empty")
        alias = x
        break
    else:
        raise ValueError("empty")
    yield "gen_nested sep"
    n = churn()
    r.v = 95
    q.v = 94
    yield f"gen_nested {alias.v} {q.v} {n}"


# a CONTAINER LITERAL source: the frame slot names its own type, which a bare
# brace init cannot deduce
def gen_literal() -> Iterator[str]:
    for x in [Cell(11), Cell(22)]:  # tpyc: ok
        r = x
        alias = x
        break
    else:
        raise ValueError("empty")
    yield "gen_literal sep"
    n = churn()
    r.v = 93
    yield f"gen_literal {alias.v} {n}"


# a SLICE source: a view whose element still belongs to `cells`, so the mutation
# must reach the original
def gen_slice() -> Iterator[str]:
    cells = make_cells()
    for x in cells[1:]:  # tpyc: ok
        r = x
        break
    else:
        raise ValueError("empty")
    yield "gen_slice sep"
    n = churn()
    r.v = 92
    yield f"gen_slice {cells[1].v} {n}"


# a CONST source (a list param): the loop keeps its borrowed holder and the
# element it lends is const, so the binding has to be spelled const too
def gen_const_param(cells: list[Cell]) -> Iterator[str]:
    for x in cells:  # tpyc: ok
        r = x
        break
    else:
        raise ValueError("empty")
    yield "gen_const_param sep"
    n = churn()
    yield f"gen_const_param {r.v} {n}"


# a `break` out of a generator-call source nothing escapes from: the callee is
# dropped at the break, so its `finally` runs there, as CPython runs it
def gen_break_finally() -> Iterator[str]:
    t = 0
    for v in counted():  # tpyc: ok
        t += v
        if v == 2:
            break
    yield f"gen_break_finally {t}"


# a slice of a list PARAM: the slot holds a VIEW, so the element it lends is
# still the caller's and a write through the binding must reach it
def gen_param_slice(cells: list[Cell]) -> Iterator[str]:
    for x in cells[1:]:  # tpyc: ok
        r = x
        break
    else:
        raise ValueError("empty")
    yield "gen_param_slice sep"
    n = churn()
    r.v = 84
    yield f"gen_param_slice {n}"


# the loop inside a `match` arm
def gen_in_match(k: int32) -> Iterator[str]:
    match k:
        case 1:
            for x in make_cells():  # tpyc: ok
                r = x
                alias = x
                break
            else:
                raise ValueError("empty")
        case _:
            raise ValueError("bad key")
    yield "gen_in_match sep"
    n = churn()
    r.v = 83
    yield f"gen_in_match {alias.v} {n}"


# a str LITERAL source with nothing escaping: a view, exactly as a plain
# function spells it -- no owning copy in the frame
def gen_str_literal() -> Iterator[str]:
    n = 0
    for ch in "abcdefghij":  # tpyc: ok
        n += 1
    yield f"gen_str_literal {n}"


# a self-recursive generator whose delegating loop does NOT suspend: the callee
# frame is not embedded, so the recursion stays finite-size
def rsum(n: int32) -> Iterator[int32]:
    if n <= 0:
        yield 1
        return
    t = 0
    for v in rsum(n - 1):  # tpyc: ok
        t += v
    yield t + 1


# a lazy combinator over a temporary, nothing escaping: the holder stays in the
# state block, as for any source the body lends nothing out of; the temporary
# the combinator retains is seated on the frame regardless
def gen_combinator() -> Iterator[str]:
    t = 0
    for i, v in enumerate(make_values()):  # tpyc: ok
        t += i * 100 + v
    yield "gen_combinator sep"
    n = churn()
    yield f"gen_combinator {t} {n}"


# a USER-defined iterable handed over as a temporary: the frame emplaces the
# source and walks it through `__iter__`/`__next__`. `held` aliases the element,
# so the write through it is read back through the loop var.
def gen_user_iter() -> Iterator[str]:
    t = 0
    for x in make_feed():  # tpyc: ok
        held = x
        held.v += 100
        t += x.v
    yield "gen_user_iter sep"
    n = churn()
    yield f"gen_user_iter {t} {n}"


# a `Ptr[T]` bound from an element: the frame field OWNS a pointer, and what it
# points at is the element -- so the source still has to outlive the block
def gen_ptr_local() -> Iterator[str]:
    for x in make_cells():  # tpyc: ok
        p: Ptr[Cell] = x
        q: Ptr[Cell] = x
        break
    else:
        raise ValueError("empty")
    yield "gen_ptr_local sep"
    n = churn()
    p.v = 82
    yield f"gen_ptr_local {q.v} {n}"


# a RECORD holding a `Ptr` to the element: an owning frame slot whose value
# points outside the frame, same as the bare pointer above
def gen_ptr_field() -> Iterator[str]:
    for x in make_cells():  # tpyc: ok
        h = Holder(x)
        g = Holder(x)
        break
    else:
        raise ValueError("empty")
    yield "gen_ptr_field sep"
    n = churn()
    h.c.v = 81
    yield f"gen_ptr_field {g.c.v} {n}"


# a str temporary iterated by char: a value element, copied into the frame
def gen_value_elem() -> Iterator[str]:
    for ch in make_name():  # tpyc: ok
        c = ch
        break
    else:
        raise ValueError("empty")
    yield "gen_value_elem sep"
    n = churn()
    yield f"gen_value_elem {c} {n}"


# a NAMED source must keep aliasing: the mutation is visible to the caller
def gen_named(cells: list[Cell]) -> Iterator[str]:
    for x in cells:  # tpyc: ok
        r = x
        break
    else:
        raise ValueError("empty")
    yield "gen_named sep"
    n = churn()
    r.v = 91
    yield f"gen_named {n}"


# bind and read inside ONE state block, before any suspension
def gen_same_block() -> Iterator[str]:
    for x in make_cells():  # tpyc: ok
        r = x
        break
    else:
        raise ValueError("empty")
    v = r.v
    yield f"gen_same_block {v}"


class Bag:
    cells: list[Cell]

    def __init__(self) -> None:
        self.cells = [Cell(11), Cell(22)]

    # generator METHOD over a fresh source
    def rows(self) -> Iterator[str]:
        for x in make_cells():  # tpyc: ok
            r = x
            alias = x
            break
        else:
            raise ValueError("empty")
        yield "gen_method sep"
        n = churn()
        r.v = 90
        yield f"gen_method {alias.v} {n}"

    # a self FIELD source under a generator method: const, like the param form
    def own_rows(self) -> Iterator[str]:
        for x in self.cells:  # tpyc: ok
            r = x
            break
        else:
            raise ValueError("empty")
        yield "gen_self_field sep"
        n = churn()
        yield f"gen_self_field {r.v} {n}"

    # async METHOD
    async def total(self) -> int32:
        for x in make_cells():  # tpyc: ok
            r = x
            alias = x
            break
        else:
            raise ValueError("empty")
        n = churn()
        await asyncio.sleep(0)
        r.v = 89
        return alias.v + n


# the loop inside a `try`
def gen_in_try() -> Iterator[str]:
    try:
        for x in make_cells():  # tpyc: ok
            r = x
            alias = x
            break
        else:
            raise ValueError("empty")
    except ValueError:
        raise
    yield "gen_in_try sep"
    n = churn()
    r.v = 88
    yield f"gen_in_try {alias.v} {n}"


class Scope:
    def __enter__(self) -> int32:
        return 1

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


# the loop inside a `with`
def gen_in_with() -> Iterator[str]:
    with Scope() as one:
        for x in make_cells():  # tpyc: ok
            r = x
            alias = x
            break
        else:
            raise ValueError("empty")
    yield "gen_in_with sep"
    n = churn()
    r.v = 87
    yield f"gen_in_with {alias.v} {one} {n}"


# async FUNCTION
async def async_fn() -> int32:
    for x in make_cells():  # tpyc: ok
        r = x
        alias = x
        break
    else:
        raise ValueError("empty")
    n = churn()
    await asyncio.sleep(0)
    r.v = 86
    return alias.v + n


async def run_async() -> None:
    print("async_fn", await async_fn())
    bag = Bag()
    print("async_method", await bag.total())


def main() -> None:
    for s in gen_plain():
        print(s)
    for s in gen_unpack():
        print(s)
    for s in gen_field():
        print(s)
    for s in gen_two_arms(True):
        print(s)
    for s in gen_nested():
        print(s)
    for s in gen_literal():
        print(s)
    for s in gen_slice():
        print(s)
    cells = make_cells()
    for s in gen_const_param(cells):
        print(s)
    for s in gen_break_finally():
        print(s)
    sliced = make_cells()
    for s in gen_param_slice(sliced):
        print(s)
    print("gen_param_slice_source", sliced[1].v)
    for s in gen_in_match(1):
        print(s)
    for s in gen_str_literal():
        print(s)
    for v in rsum(3):
        print("rsum", v)
    for s in gen_combinator():
        print(s)
    for s in gen_user_iter():
        print(s)
    for s in gen_ptr_local():
        print(s)
    for s in gen_ptr_field():
        print(s)
    for s in gen_value_elem():
        print(s)
    named = make_cells()
    for s in gen_named(named):
        print(s)
    print("gen_named_source", named[0].v)
    for s in gen_same_block():
        print(s)
    bag = Bag()
    for s in bag.rows():
        print(s)
    for s in bag.own_rows():
        print(s)
    for s in gen_in_try():
        print(s)
    for s in gen_in_with():
        print(s)
    asyncio.run(run_async())


main()
