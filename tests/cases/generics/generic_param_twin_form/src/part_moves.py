# A by-value parameter is copied or moved where the function written for the
# concrete type copies or moves it: a plain `return x`, a frame constructor, a
# `@dynamic` adapter and a reassigned parameter's prologue move; a last use in
# the body (an argument, a factory's pass) copies. Only the callee's own value
# ever moves: each caller's argument prints intact.
import asyncio
from typing import Callable, Iterator, Protocol
from tpy import Own, ValueType, dynamic, int32
from fixtures import V


class Hold[T]:
    def __init__(self, v: T) -> None:
        # the hedged copy warning is for a reference `T`; a value one copies
        self.v = v  # tpyc: warning(/may copy T into field/)


def ret[T](x: T) -> T:
    return x


def keep[T](x: T) -> Own[Hold[T]]:
    # the last use passes the value to the constructor (a copy, as the twin's)
    return Hold(x)


def gen[T](x: T) -> Iterator[T]:
    yield x


async def co[T](x: T) -> T:
    await asyncio.sleep(0)
    return x


def rebind[T: ValueType](x: T, y: T) -> T:
    # reassigned parameter: the prologue copy takes the parameter over
    x = y
    return x


def app[A, R](f: Callable[[A], R], a: A) -> R:
    # a lambda parameter at `A = int32` takes the argument by value
    return f(a)


@dynamic
class Sink[T](Protocol):
    def put(self, x: T) -> int32: ...


class VSink:
    def put(self, x: V) -> int32:
        return len(x.s)


def use_sink(k: Sink[V], v: V) -> int32:
    # the adapter forwards its own parameter to the conformer
    return k.put(v)


async def co_main(v: V) -> None:
    print("move async", (await co(v)).s, v.s)


def main() -> None:
    v = V("moved value")
    print("move ret", ret(v).s, v.s)
    print("move keep", keep(v).v.s, v.s)
    for e in gen(v):
        print("move generator", e.s, v.s)
    asyncio.run(co_main(v))
    w = V("other")
    print("move rebind", rebind(v, w).s, v.s, w.s)
    print("move rebind str", rebind("a", "b"))
    print("move app", app(lambda x: x + 1, 4))
    print("move dynamic", use_sink(VSink(), v), v.s)
