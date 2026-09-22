# A `with` manager that is a borrow-returning METHOD call binds the manager
# BORROWED, like the @property spelling of the same accessor: __enter__ and
# __exit__ run on the object the callee lent, not on a copy of it. Every
# section reads the source guard back after the block, so a copy would show.
from tpy import int32, nocopy
from typing import Iterator

import asyncio


class Guard:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def __enter__(self) -> int32:
        self.n += 1
        return self.n

    def __exit__(self, kind, value, tb) -> None:
        self.n += 10


@nocopy
class NcGuard:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def __enter__(self) -> int32:
        self.n += 1
        return self.n

    def __exit__(self, kind, value, tb) -> None:
        self.n += 10


class B:
    _guard: Guard
    _nc: NcGuard

    def __init__(self) -> None:
        self._guard = Guard()
        self._nc = NcGuard()

    def guard_m(self) -> Guard:
        return self._guard

    def nc_m(self) -> NcGuard:
        return self._nc


class Host:
    k: int32

    # constructor position
    def __init__(self, b: B) -> None:
        with b.guard_m() as q:  # tpyc: ok
            print("ctor", q)
        self.k = b._guard.n

    # method position
    def run(self, b: B) -> None:
        with b.guard_m() as q:  # tpyc: ok
            print("method", q)
        print("method after", b._guard.n)


# free function
def func(b: B) -> None:
    with b.guard_m() as q:  # tpyc: ok
        print("func", q)
    print("func after", b._guard.n)


# no `as` target
def no_target(b: B) -> None:
    with b.guard_m():  # tpyc: ok
        print("no_target", b._guard.n)
    print("no_target after", b._guard.n)


# a @nocopy manager: a by-value ctx slot would not even compile
def nocopy_m(b: B) -> None:
    with b.nc_m() as q:  # tpyc: ok
        print("nocopy", q)
    print("nocopy after", b._nc.n)


# nested def
def closure(b: B) -> None:
    def inner() -> None:
        with b.guard_m() as q:  # tpyc: ok
            print("closure", q)
    inner()
    print("closure after", b._guard.n)


# generator body (a with region with no suspension in it)
def gen(b: B) -> Iterator[int32]:
    with b.guard_m() as q:  # tpyc: ok
        print("gen", q)
    yield b._guard.n


# async body
async def coro(b: B) -> None:
    with b.guard_m() as q:  # tpyc: ok
        print("async", q)
    print("async after", b._guard.n)


def main() -> None:
    b = B()
    func(b)
    no_target(b)
    nocopy_m(b)
    closure(b)
    h = Host(b)
    print("ctor after", h.k)
    h.run(b)
    for v in gen(b):
        print("gen after", v)
    asyncio.run(coro(b))


main()

# module level
top = B()
with top.guard_m() as t:  # tpyc: ok
    print("module", t)
print("module after", top._guard.n)
