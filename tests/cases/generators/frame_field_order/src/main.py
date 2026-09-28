# A generator or coroutine frame keeps what it borrows alive and releases a
# loop's held source generator when CPython does, as the loop exits. Each
# inner `finally` reads the storage it borrows.
import asyncio
from typing import Iterator

from tpy import int32

GLOBAL_XS: list[int32] = [30, 31]


def inner(tag: str, xs: list[int32]) -> Iterator[int32]:
    try:
        for x in xs:
            yield x
    finally:
        print(tag, "inner finally", len(xs), xs[0])


def rows_outer(rows: list[list[int32]]) -> Iterator[int32]:
    for row in rows:
        g = inner("loopvar", row)
        # The subject: a loop over a generator that borrows the loop variable.
        for v in g:  # tpyc: ok
            yield v
    yield 99


def global_outer() -> Iterator[int32]:
    g = inner("global", GLOBAL_XS)
    # The subject: a loop over a generator that borrows a module global.
    for v in g:  # tpyc: ok
        yield v
    yield 99


def rebound_outer(c: bool) -> Iterator[int32]:
    xs = [1, 2]
    ys = xs
    if c:
        xs = [3, 4]
    g = inner("rebound", xs)
    # The subject: a loop over a generator that borrows a rebound local.
    for v in g:  # tpyc: ok
        yield v
    yield ys[0]


def break_outer(rows: list[list[int32]]) -> Iterator[int32]:
    for row in rows:
        xs = [row[0], row[1]]
        # The subject: `break` releases the loop's source before the next
        # pass refills `xs`.
        for v in inner("break", xs):
            yield v
            break
        print("break after inner loop")
    yield 99


def return_outer(xs: list[int32]) -> Iterator[int32]:
    try:
        # The subject: `return` releases the source before the `finally`.
        for v in inner("return", xs):
            yield v
            return
    finally:
        print("return outer finally")


def exc_outer(xs: list[int32]) -> Iterator[int32]:
    try:
        # The subject: an exception releases the source before its handler.
        for v in inner("exc", xs):
            yield v
            raise ValueError("stop")
    except ValueError:
        print("exc caught")
    yield 100


class Holder:
    rows: list[list[int32]]

    def __init__(self) -> None:
        self.rows = [[40, 41], [42, 43]]

    def items(self) -> Iterator[int32]:
        for row in self.rows:
            g = inner("method", row)
            # The subject: the generator-method twin of `rows_outer`.
            for v in g:  # tpyc: ok
                yield v
        yield 99


async def async_rows(rows: list[list[int32]]) -> int32:
    total = 0
    for row in rows:
        g = inner("async", row)
        # The subject: a loop over a held generator inside an async def.
        for v in g:  # tpyc: ok
            await asyncio.sleep(0)
            total += v
        xs = [row[1], row[0]]
        # The subject: `break` releases the source in an async def too.
        for v in inner("async break", xs):
            await asyncio.sleep(0)
            total += v
            break
        print("async after inner loop")
    return total


def first_of(tag: str, g: Iterator[int32]) -> None:
    for v in g:
        print(tag, "first", v)
        break


# Each abandons its frame part-way: it dies suspended inside its loop.
def abandon_loopvar(rows: list[list[int32]]) -> None:
    o = rows_outer(rows)
    first_of("loopvar", o)


def abandon_global() -> None:
    o = global_outer()
    first_of("global", o)


def abandon_rebound() -> None:
    o = rebound_outer(True)
    first_of("rebound", o)


def abandon_break(rows: list[list[int32]]) -> None:
    o = break_outer(rows)
    first_of("break", o)


def abandon_return(xs: list[int32]) -> None:
    o = return_outer(xs)
    first_of("return", o)


def abandon_exc(xs: list[int32]) -> None:
    o = exc_outer(xs)
    first_of("exc", o)


def abandon_method(h: Holder) -> None:
    o = h.items()
    first_of("method", o)


def main() -> None:
    rows = [[1, 2], [3, 4]]
    loopvar_vals = list(rows_outer(rows))
    print("loopvar", loopvar_vals)
    abandon_loopvar(rows)
    global_vals = list(global_outer())
    print("global", global_vals)
    abandon_global()
    rebound_vals = list(rebound_outer(True))
    print("rebound", rebound_vals)
    abandon_rebound()
    break_vals = list(break_outer(rows))
    print("break", break_vals)
    abandon_break(rows)
    xs = [5, 6]
    return_vals = list(return_outer(xs))
    print("return", return_vals)
    abandon_return(xs)
    exc_vals = list(exc_outer(xs))
    print("exc", exc_vals)
    abandon_exc(xs)
    h = Holder()
    method_vals = list(h.items())
    print("method", method_vals)
    abandon_method(h)
    total = asyncio.run(async_rows(rows))
    print("async", total)


main()
