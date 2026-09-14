# Same-module free calls must emit the namespace-qualified callee, so ADL cannot
# pull a same-named std::/::tpy:: template into the overload set and win.
import asyncio
from typing import Callable, Iterator

from tpy import int32, Own
from tpy.coro import Cancellable


def double(x: int32) -> int32:
    return x * 2


# `invoke` collides with std::invoke; the std::function argument makes `std` an
# associated namespace and the variadic forwarding-ref std::invoke outranks this
# signature, so a bare call silently invokes the callable instead of this body.
def invoke(f: Callable[[int32], int32], v: int32) -> int32:
    print("invoke: user body")
    return f(v) + 100


# Same collision against std::apply, which fails to COMPILE when it wins
# (tuple_size_v on a non-tuple, outside the immediate context).
def apply(f: Callable[[int32], int32], v: int32) -> int32:
    return f(v) + 1000


# `find` collides with std::find, reached through the list argument's `std`.
def find(xs: list[int32], want: int32) -> int32:
    for i in range(len(xs)):
        if xs[i] == want:
            return i
    return -1


# `count` collides with std::count the same way; both std templates take
# iterators, so a wrong resolution is a compile error rather than a silent call.
def count(xs: list[int32], want: int32) -> int32:
    n = 0
    for x in xs:
        if x == want:
            n += 1
    return n


# A collision with a ::tpy:: runtime free template rather than a std:: one --
# the list argument makes ::tpy:: associated the same way `std` is.
def as_span(xs: list[int32]) -> int32:
    return len(xs)


# A void free call taking a MUTABLE reference: the qualified spelling must not
# disturb the by-reference argument, so the append stays visible to the caller.
def push(xs: list[int32], v: int32) -> None:
    xs.append(v)


def gen(n: int32) -> Iterator[int32]:
    for i in range(n):
        # generator frame: the free call is emitted inside the frame's __next__
        yield double(i)  # tpyc: ok


def comp(n: int32) -> int32:
    # comprehension body: the free call is emitted inside the statement-expr
    doubled = [double(i) for i in range(n)]  # tpyc: ok
    total = 0
    for v in doubled:
        total += v
    return total


def closure(n: int32) -> int32:
    def inner(k: int32) -> int32:
        return double(k) + n
    # nested def: a C++ local lambda, so this call stays BARE
    return inner(3)  # tpyc: ok


def recurse(n: int32) -> int32:
    if n <= 0:
        return 0
    # recursion: the callee is the enclosing function, qualified like any other
    return recurse(n - 1) + n  # tpyc: ok


def pick[T](a: T, b: T) -> T:
    return a


async def async_double(n: int32) -> int32:
    await asyncio.sleep(0.0)
    return n + n


async def run_factory(factory: Callable[[int32], Own[Cancellable[int32]]],
                      v: int32) -> int32:
    return await asyncio.create_task(factory(v))


async def async_main() -> int32:
    # async: passing a same-module `async def` into a factory slot synthesizes
    # a wrapper lambda whose body CALLS it -- that call is qualified too
    return await run_factory(async_double, 6)  # tpyc: ok


class Scaler:
    factor: int32

    def __init__(self, factor: int32) -> None:
        self.factor = factor

    def scaled(self, x: int32) -> int32:
        # method body (emitted inline in the header): a free call from inside a
        # record member still names the module namespace, not the record's
        return double(x) * self.factor  # tpyc: ok


def main() -> None:
    fn: Callable[[int32], int32] = double
    # the silent guard: without the qualified spelling std::invoke wins here.
    # The result is bound to a local first rather than printed inline: a print
    # argument's own output is streamed AFTER the prefix
    # (BUGS.md#subexpression-right-to-left-eval), so `print("invoke:", invoke(...))`
    # would interleave "invoke: " ahead of the body's own line and diverge from
    # CPython -- which is a different bug, not the one this case is pinning.
    got = invoke(fn, 1)  # tpyc: ok
    print("invoke:", got)
    # the loud guard: without it std::apply wins and fails to compile
    print("apply:", apply(fn, 2))  # tpyc: ok
    # a callable VALUE call stays bare -- it names a local, not a namespace member
    print("callable_value:", fn(4))  # tpyc: ok

    xs = [4, 5, 4]
    print("find:", find(xs, 5))  # tpyc: ok
    print("count:", count(xs, 4))  # tpyc: ok
    print("as_span:", as_span(xs))  # tpyc: ok
    # the list is passed by reference, so the append is visible to the caller
    push(xs, 9)  # tpyc: ok
    print("push:", xs)

    total = 0
    for y in gen(3):
        total += y
    print("generator:", total)
    print("comprehension:", comp(4))
    print("nested_def:", closure(2))
    print("recursion:", recurse(4))
    print("generic:", pick(7, 8))  # tpyc: ok
    print("method:", Scaler(3).scaled(5))
    print("async:", asyncio.run(async_main()))


main()
