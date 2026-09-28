# A generator or coroutine held in a name stays open until the name is bound
# again, deleted, or the function ends (CPython lifetime) -- except where it
# is first bound in a block. A generator or async body holds a loop-bound one
# into the next pass only when that pass writes nothing it borrows. A plain
# function keeps a block-bound one in the block's C++ scope, unless a read
# after the block declares it in front of the block, which only one that
# borrows parameters, the receiver or module globals may be. Each plain
# block that closes a generator early is its function's last statement or
# closes one whose close is unobservable
# (BUGS.md#plain-loop-generator-closes-at-pass-end,
# BUGS.md#plain-block-generator-closes-at-block-end).
import asyncio
from typing import Iterator

from tpy import Span, int32, readonly


def items(xs: list[int32]) -> Iterator[int32]:
    try:
        for x in xs:
            yield x
    finally:
        print("items finally", len(xs), xs[0])


def mutating(xs: list[int32]) -> Iterator[int32]:
    try:
        yield xs[0]
        yield xs[0] + 1
    finally:
        xs[0] = 9
        print("mutating finally")


def plain_items(xs: list[int32]) -> Iterator[int32]:
    for x in xs:
        yield x


class Cell:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


def cells() -> Iterator[Cell]:
    c = Cell(0)
    yield c
    c = Cell(10)
    yield c


def cell_gen(c: Cell) -> Iterator[int32]:
    try:
        yield c.v
        yield c.v + 1
    finally:
        print("cell finally", c.v)


class Ctx:
    def __enter__(self) -> "Ctx":
        print("ctx enter")
        return self

    def __exit__(self, et, ev, tb) -> None:
        print("ctx exit")


class Refill:
    xs: list[int32]

    def __init__(self) -> None:
        self.xs = [1, 2]

    def __enter__(self) -> "Refill":
        return self

    def __exit__(self, et, ev, tb) -> None:
        self.xs = [7, 8]


class Tally:
    xs: list[int32]
    n: int32

    def __init__(self) -> None:
        self.xs = [3, 4]
        self.n = 0

    def __enter__(self) -> "Tally":
        return self

    def __exit__(self, et, ev, tb) -> None:
        self.n += 1
        print("tally exit", self.n)


class Noisy:
    tag: int32

    def __init__(self, tag: int32) -> None:
        self.tag = tag

    def __del__(self) -> None:
        print("noisy del", self.tag)


def holding(xs: list[int32]) -> Iterator[int32]:
    # No finally: closing it runs user code through the held local's __del__.
    n = Noisy(xs[0])
    for x in xs:
        yield x + n.tag


def watched(xs: list[int32], done: list[int32]) -> Iterator[int32]:
    try:
        for x in xs:
            yield x
    finally:
        done.append(len(xs))
        print("watched finally", len(done))


def first(g: Iterator[int32]) -> int32:
    for v in g:
        return v
    return -1


def cell_items(xs: list[Cell], log: list[int32]) -> Iterator[int32]:
    for c in xs:
        try:
            yield c.v
        finally:
            log.append(c.v)


# --- plain function ---

def plain_branch(c: bool, xs: list[int32]) -> None:
    if c:
        ys = [10, 11]
        # A generator over a block local, bound in both branches and used
        # only in them: each branch keeps its own and closes it at its end.
        g = items(ys)  # tpyc: ok
        print("plain_branch", first(g))
    else:
        g = items(xs)
        print("plain_branch", first(g))


def plain_branch_param(c: bool, xs: list[int32]) -> None:
    if c:
        # Borrows only a parameter the function never rebinds, so the read
        # after the block declares it in front of the block: open until the
        # function ends, as in Python.
        g = items(xs)  # tpyc: ok
    else:
        g = items(xs)
    print("plain_branch_param", first(g))
    print("plain_branch_param after")


def plain_with() -> None:
    with Ctx():
        ys = [20, 21]
        # A with-body generator over a with-body local stays the block's and
        # closes before the exit (it runs nothing when it closes).
        g = plain_items(ys)  # tpyc: ok
        print("plain_with", first(g))


def plain_with_exit() -> None:
    r = Refill()
    with r:
        # g borrows the local r, so it stays the block's and closes before
        # the exit replaces r.xs.
        g = plain_items(r.xs)  # tpyc: ok
        print("plain_with_exit", first(g))
    print("plain_with_exit after", r.xs[0])


def plain_with_param(t: Tally) -> None:
    with t:
        # Borrows only a parameter: declared in front of the block and open
        # until the function ends, as in Python.
        g = items(t.xs)  # tpyc: ok
        print("plain_with_param", first(g))
    print("plain_with_param after", first(g))


def plain_finally(xs: list[int32]) -> None:
    if len(xs) > 0:
        # Closes with the block, the function's last statement: its finally
        # has run when the caller reads xs.
        g = mutating(xs)  # tpyc: ok
        print("plain_finally", first(g))


def plain_block_then_grow(k: bool, log: list[int32]) -> None:
    xs = [Cell(1), Cell(2)]
    if k:
        # Closes with the block, before the loop below grows xs under the
        # element its finally reads.
        g = cell_items(xs, log)  # tpyc: ok
        print("plain_block_then_grow", first(g))
    for i in range(100):
        xs.append(Cell(9))
    print("plain_block_then_grow end", len(xs))


def plain_refill(rows: list[list[int32]]) -> None:
    for row in rows:
        xs = [row[0], row[1]]
        # Kept in the pass's block: gone before the next pass refills xs.
        g = items(xs)  # tpyc: ok
        print("plain_refill", first(g))


def plain_refill_del(rows: list[list[int32]]) -> None:
    for row in rows:
        xs = [row[0], row[1]]
        g = items(xs)
        print("plain_refill_del", first(g))
        # Closed where the code says, as CPython closes it.
        del g  # tpyc: ok
    print("plain_refill_del after")


def plain_param_after_loop(xs: list[int32]) -> None:
    for i in range(2):
        # Borrows only a parameter the function never rebinds, so the last
        # pass's generator stays open after the loop, as in CPython.
        g = items(xs)  # tpyc: ok
        print("plain_param_after_loop", i, first(g))
    v = first(g)
    print("plain_param_after_loop after", v)


def spans(xs: Span[readonly[int32]]) -> Iterator[int32]:
    for x in xs:
        yield x


def gen_span_arg() -> Iterator[int32]:
    a = [1, 2]
    for i in range(2):
        # A list coerced to a Span argument borrows the list itself: the
        # loop writes nothing, so the generator may stay open across passes.
        g = spans(a)  # tpyc: ok
        yield first(g)


def plain_loop_var() -> None:
    for c in cells():
        # Borrows the pulled element: gone with the pass, before the next pull.
        g = cell_gen(c)  # tpyc: ok
        print("plain_loop_var", first(g))


def plain_alias(xs: list[int32]) -> None:
    g = items(xs)
    # Another name for the same generator: pulling one advances the other.
    h = g  # tpyc: ok
    print("plain_alias", first(g), first(h))


def plain_del(xs: list[int32]) -> None:
    g = mutating(xs)
    print("plain_del", first(g))
    # Closes the started generator here: its finally runs before the print.
    del g  # tpyc: ok
    print("plain_del after", xs[0])


def plain_abandon(rows: list[list[int32]]) -> None:
    for row in rows:
        xs = [row[0], row[1]]
        g = items(xs)
        print("plain_abandon", first(g))
        # Leaving the loop by return closes this pass's g too.
        if row[0] > 1:  # tpyc: ok
            return


def plain_refill_holder(rows: list[list[int32]]) -> None:
    for row in rows:
        xs = [row[0], row[1]]
        # The pass end closes a frame that holds a class with __del__.
        g = holding(xs)  # tpyc: ok
        print("plain_refill_holder", first(g))


# --- generator ---

def gen_branch(c: bool, xs: list[int32]) -> Iterator[int32]:
    if c:
        ys = [30, 31]
        g = items(ys)  # tpyc: ok
    else:
        g = items(xs)
    for v in g:
        yield v


def gen_finally(xs: list[int32]) -> Iterator[int32]:
    if len(xs) > 0:
        g = mutating(xs)  # tpyc: ok
        yield first(g)
    yield xs[0]


def gen_rows(rows: list[list[int32]]) -> Iterator[int32]:
    for row in rows:
        print("gen_rows pass", row[0])
        # The pass writes nothing g borrows: the previous pass's g stays open
        # until the next pass binds g again, as in Python.
        g = items(row)  # tpyc: ok
        yield first(g)
        yield first(g)


def gen_refill_del(rows: list[list[int32]]) -> Iterator[int32]:
    for row in rows:
        xs = [row[0], row[1]]
        g = items(xs)  # tpyc: ok
        for v in g:
            yield v
        # Closed before the next pass refills xs.
        del g


def gen_loop_var_del() -> Iterator[int32]:
    src = cells()
    for c in src:
        g = cell_gen(c)  # tpyc: ok
        yield first(g)
        # Closed before the next pull rebuilds what c refers to.
        del g


def gen_rebuild(xs: list[int32]) -> Iterator[int32]:
    done: list[int32] = []
    while not done:
        # Building the next g runs nothing: binding it closes the previous
        # one (whose finally writes done) before the new one starts.
        g = watched(xs, done)  # tpyc: ok
        yield first(g)


def gen_with_scalar_exit() -> Iterator[int32]:
    t = Tally()
    with t:
        # The generator-body twin: g stays open past the block.
        g = items(t.xs)  # tpyc: ok
        yield first(g)
    yield first(g)


class Lookup:
    base: int32

    def __init__(self, base: int32) -> None:
        self.base = base

    def __getitem__(self, i: int32) -> int32:
        return self.base + i

    def __contains__(self, i: int32) -> bool:
        return i < self.base


def gen_user_read(xs: list[int32], t: Lookup) -> Iterator[int32]:
    for i in range(3):
        g = items(xs)  # tpyc: ok
        yield first(g)
        # The subject: a user __getitem__ and __contains__ that write nothing
        # g borrows keep g open into the next pass.
        if i in t:
            yield t[i]


def gen_del(xs: list[int32]) -> Iterator[int32]:
    g = mutating(xs)
    yield first(g)
    del g  # tpyc: ok
    yield xs[0]


# --- generator method ---

class Rows:
    rows: list[list[int32]]
    seen: int32

    def __init__(self) -> None:
        self.rows = [[5, 6], [7, 8]]
        self.seen = 0

    def walk(self) -> Iterator[int32]:
        for row in self.rows:
            # The method twin of gen_rows: the loop steps row through
            # self.rows and stores only into another field.
            g = items(row)  # tpyc: ok
            for v in g:
                yield v
            self.seen += 1

    def first_row(self) -> Iterator[int32]:
        for x in self.rows[0]:
            yield x

    def walk_count(self) -> Iterator[int32]:
        for i in range(2):
            g = self.first_row()  # tpyc: ok
            for v in g:
                # A scalar field store moves nothing g borrows.
                self.seen += 1
                yield v

    def walk_plain(self) -> Iterator[int32]:
        for row in self.rows:
            xs = [row[0], row[1]]
            g = plain_items(xs)  # tpyc: ok
            for v in g:
                yield v
            # The method twin of gen_refill_del.
            del g


# --- async def ---

async def work(xs: list[int32]) -> int32:
    try:
        await asyncio.sleep(0)
        return xs[0]
    finally:
        print("work finally", xs[0])


async def async_loop(rows: list[list[int32]]) -> None:
    for row in rows:
        xs = [row[0]]  # tpyc: ok
        # A coroutine held in a loop body, awaited in its pass.
        c = work(xs)  # tpyc: ok
        print("async_loop", await c)


async def async_gen_refill(rows: list[list[int32]]) -> None:
    for row in rows:
        xs = [row[0], row[1]]
        g = items(xs)  # tpyc: ok
        print("async_gen_refill", first(g))
        await asyncio.sleep(0)
        # Closed before the next pass refills xs.
        del g


def drive(tag: str, g: Iterator[int32]) -> None:
    for v in g:
        print(tag, v)


def abandon(tag: str, g: Iterator[int32]) -> None:
    v = first(g)
    print(tag, "abandoned at", v)


# Each generator section runs to the end, then again abandoned part-way;
# each run has a function of its own, whose return ends the generator.
def run_gen_branch() -> None:
    drive("gen_branch", gen_branch(True, [1, 2]))


def abandon_gen_branch() -> None:
    abandon("gen_branch", gen_branch(False, [1, 2]))


def run_gen_finally() -> None:
    drive("gen_finally", gen_finally([0]))


def abandon_gen_finally() -> None:
    abandon("gen_finally", gen_finally([0]))


def run_gen_rows(rows: list[list[int32]]) -> None:
    drive("gen_rows", gen_rows(rows))


def abandon_gen_rows(rows: list[list[int32]]) -> None:
    abandon("gen_rows", gen_rows(rows))


def run_gen_refill_del(rows: list[list[int32]]) -> None:
    drive("gen_refill_del", gen_refill_del(rows))


def abandon_gen_refill_del(rows: list[list[int32]]) -> None:
    abandon("gen_refill_del", gen_refill_del(rows))


def run_gen_loop_var_del() -> None:
    drive("gen_loop_var_del", gen_loop_var_del())


def abandon_gen_loop_var_del() -> None:
    abandon("gen_loop_var_del", gen_loop_var_del())


def run_gen_rebuild() -> None:
    drive("gen_rebuild", gen_rebuild([1, 2]))


def abandon_gen_rebuild() -> None:
    abandon("gen_rebuild", gen_rebuild([1, 2]))


def run_gen_with_scalar_exit() -> None:
    drive("gen_with_scalar_exit", gen_with_scalar_exit())


def run_gen_user_read() -> None:
    drive("gen_user_read", gen_user_read([4, 5], Lookup(2)))


def run_gen_del() -> None:
    drive("gen_del", gen_del([0]))


def abandon_gen_del() -> None:
    abandon("gen_del", gen_del([0]))


def run_method(r: Rows) -> None:
    w = r.walk()
    drive("method", w)


def abandon_method(r: Rows) -> None:
    w = r.walk()
    abandon("method", w)


def run_method_count(r: Rows) -> None:
    w = r.walk_count()
    drive("method_count", w)
    print("method_count seen", r.seen)


def run_method_plain(r: Rows) -> None:
    w = r.walk_plain()
    drive("method_plain", w)


def main() -> None:
    rows = [[1, 2], [3, 4]]
    plain_branch(True, [1, 2])
    plain_branch(False, [1, 2])
    plain_branch_param(True, [1, 2])
    plain_with()
    plain_with_exit()
    plain_with_param(Tally())
    fxs = [0]
    plain_finally(fxs)
    print("plain_finally after", fxs[0])
    log: list[int32] = []
    plain_block_then_grow(True, log)
    print("plain_block_then_grow log", log[0])
    plain_refill(rows)
    print("plain_refill after")
    plain_refill_del(rows)
    plain_param_after_loop([5, 6])
    plain_loop_var()
    print("plain_loop_var after")
    plain_alias([1, 2, 3])
    plain_del([0])
    plain_abandon(rows)
    print("plain_abandon after")
    plain_refill_holder(rows)
    print("plain_refill_holder after")
    run_gen_branch()
    abandon_gen_branch()
    run_gen_finally()
    abandon_gen_finally()
    run_gen_rows(rows)
    drive("gen_span_arg", gen_span_arg())
    abandon_gen_rows(rows)
    run_gen_refill_del(rows)
    abandon_gen_refill_del(rows)
    run_gen_loop_var_del()
    abandon_gen_loop_var_del()
    run_gen_rebuild()
    abandon_gen_rebuild()
    run_gen_with_scalar_exit()
    run_gen_user_read()
    run_gen_del()
    abandon_gen_del()
    r = Rows()
    run_method(r)
    abandon_method(r)
    run_method_plain(r)
    print("method seen", r.seen)
    run_method_count(r)
    asyncio.run(async_loop(rows))
    asyncio.run(async_gen_refill(rows))


main()
