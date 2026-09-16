# Passing your own parameter to an IMPORTED class's mutating method must mark
# that parameter mutated, so the caller's signature stays `Rec&`. The defining
# module finishes sema first, so auto-const inference has already set
# `is_readonly` on `Factory.touch` (it mutates its ARG, not self) by the time
# this module records its call edges; reading that receiver flag as "mutates
# nothing" dropped the edge and emitted `const Rec&` here, which does not build.
# One section per position; each mutates through the imported callee and the
# caller observes the change, so a copy would show up in the output.
import asyncio
from typing import Iterator
from tpy import int32
from keeper import Rec, Factory, Anchor, touch_free

G = Factory()


class Holder:
    f: Factory

    def __init__(self) -> None:
        self.f = Factory()

    # method: the imported method is reached through a field of self
    def go(self, r: Rec) -> None:
        self.f.touch(r)


class Guard:
    def __enter__(self) -> "Guard":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


# free function: imported class method via a local instance
def via_local(r: Rec) -> int32:  # tpyc: ok
    f = Factory()
    f.touch(r)
    return r.v


# method call chain: the mutation must reach through Holder.go to this param
def via_field(r: Rec) -> int32:  # tpyc: ok
    h = Holder()
    h.go(r)
    return r.v


# module global holding the imported class
def via_global(r: Rec) -> int32:  # tpyc: ok
    G.touch(r)
    return r.v


# staticmethod: never auto-const-inferred, so it always propagated
def via_static(r: Rec) -> int32:  # tpyc: ok
    Factory.stouch(r)
    return r.v


# classmethod
def via_class(r: Rec) -> int32:  # tpyc: ok
    Factory.ctouch(r)
    return r.v


# super(): the inherited method is itself auto-const-inferred
def via_super(r: Rec) -> int32:  # tpyc: ok
    f = Factory()
    f.via_super(r)
    return r.v


# constructor: __init__ is never auto-const-inferred
def via_ctor(r: Rec) -> int32:  # tpyc: ok
    a = Anchor(r)
    return a.seen


# comprehension
def via_comp(r: Rec) -> int32:  # tpyc: ok
    f = Factory()
    ys = [f.touch_get(r) for _ in range(1)]
    return ys[0]


# with body
def via_with(r: Rec) -> int32:  # tpyc: ok
    f = Factory()
    with Guard():
        f.touch(r)
    return r.v


# try/finally
def via_finally(r: Rec) -> int32:  # tpyc: ok
    f = Factory()
    try:
        f.touch(r)
    finally:
        pass
    return r.v


# cross-module FREE function: the leg that always worked, kept as the control
def via_free(r: Rec) -> int32:  # tpyc: ok
    touch_free(r)
    return r.v


# generator body
def via_gen(r: Rec) -> Iterator[int32]:  # tpyc: ok
    f = Factory()
    f.touch(r)
    yield r.v


# async body
async def via_async(r: Rec) -> int32:  # tpyc: ok
    f = Factory()
    f.touch(r)
    return r.v


async def main_coro() -> None:
    a = Rec(0)
    print("local", via_local(a))
    print("field", via_field(a))
    print("global", via_global(a))
    print("static", via_static(a))
    print("class", via_class(a))
    print("super", via_super(a))
    print("ctor", via_ctor(a))
    print("comp", via_comp(a))
    print("with", via_with(a))
    print("finally", via_finally(a))
    print("free", via_free(a))
    for x in via_gen(a):
        print("gen", x)
    print("async", await via_async(a))
    print("total", a.v)


def main() -> None:
    asyncio.run(main_coro())


main()
