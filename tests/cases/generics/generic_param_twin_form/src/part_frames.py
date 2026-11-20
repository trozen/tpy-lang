# `readonly[T]` generator and coroutine frames take the parameter over as a
# plain `T` frame does (at a ValueType and at `bytes`); a constructor's
# member-init builds an open-`T` member through the open-`T` sink; a reassigned
# parameter's prologue at `int` (a `const&` slot: it copies; `Own[str]` in
# generics/generic_param_twin_form_spelled); and a value `T` read while a view
# of it is alive is copied, not moved, at the call.
import asyncio
from typing import Callable, Iterator
from tpy import StrView, ValueType, readonly
from fixtures import C, Named, V


def ro_gen[T](x: readonly[T]) -> Iterator[readonly[T]]:
    yield x


async def ro_co[T](x: readonly[T]) -> readonly[T]:
    await asyncio.sleep(0)
    return x


class Hold[T]:
    def __init__(self, v: T) -> None:
        # the member-init copies, as the twin's `: v(v)`; moves at `Own[X]`
        self.v = v  # tpyc: warning(/may copy T into field/)


def rebind[T: ValueType](x: T, y: T) -> T:
    x = y
    return x


def eat(v: V) -> bool:
    return v.s == ""


def view[T: Named](x: T, g: Callable[[T], bool]) -> None:
    w: StrView = x.get()
    # the call copies `x` (as the twin's `g(x)`), so `w` still reads it below
    g(x)  # tpyc: ok
    print("view live", w)


async def co_main() -> None:
    v = V("rv")
    print("ro async V", (await ro_co(v)).s, v.s)
    print("ro async bytes", await ro_co(b"ab"))


def main() -> None:
    v = V("rv")
    for e in ro_gen(v):
        print("ro generator V", e.s, v.s)
    for bs in ro_gen(b"cd"):
        print("ro generator bytes", bs)
    asyncio.run(co_main())
    print("init bytes", Hold(b"world").v)
    hv = V("hv")
    print("init V", Hold(hv).v.s, hv.s)
    print("init str", Hold("hs").v)
    # a class `T`: the member is the warned copy, so no write is observed
    print("init class", Hold(C(1)).v.n)
    big: int = 10 ** 20
    print("rebind int", rebind(big, 3), big)
    view(V("viewed"), eat)
