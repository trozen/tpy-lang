# A call that BORROWS a pointer-repr `Optional` result already is the `T*` the
# return spells, so it passes through bare. The sections mutate through the
# returned pointer and read the source afterwards, so a copy instead of the
# alias would show. A record METHOD lending a PARAMETER's Optional is inferred
# const for its receiver only, so its result stays a mutable `R*`; the
# DECLARED `@readonly` twin is the other half -- it const-projects, so its
# result binds `const R*` and the section reads through it while the source
# is mutated by another path. (A method
# relaying a FREE function's result is left out: free functions are analyzed
# after methods, so the relay's borrow source is not recorded yet --
# BUGS.md#return-borrow-of-pending-callee-unrecorded.)
from typing import Iterator, Optional

import asyncio

from tpy import int32, readonly


class R:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class B:
    _opt: Optional[R]
    _xs: Optional[list[int32]]

    def __init__(self) -> None:
        self._opt = R(4)
        self._xs = [1]

    def opt_m(self) -> Optional[R]:
        return self._opt

    def opt_list_m(self) -> Optional[list[int32]]:
        return self._xs

    # method, borrowing through self -- the host the const twin handles
    def ret_self(self) -> Optional[R]:
        return self.opt_m()  # tpyc: ok

    # DECLARED readonly: the borrowed result is const-projected, so a local
    # bound to it is `const R*` rather than the mutable `R*` above
    @readonly
    def opt_ro(self) -> Optional[R]:
        return self._opt


def free_opt(b: B) -> Optional[R]:
    return b._opt


class Host:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    # record method, method-call source off a PARAMETER
    def via_method(self, b: B) -> Optional[R]:
        return b.opt_m()  # tpyc: ok

    # record method, Optional[container] result
    def via_container(self, b: B) -> Optional[list[int32]]:
        return b.opt_list_m()  # tpyc: ok


# free function, method-call source
def via_method(b: B) -> Optional[R]:
    return b.opt_m()  # tpyc: ok


# free function, free-call source
def via_free(b: B) -> Optional[R]:
    return free_opt(b)  # tpyc: ok


# free function, Optional[container] result
def via_container(b: B) -> Optional[list[int32]]:
    return b.opt_list_m()  # tpyc: ok


# generator body
def in_generator(b: B) -> Iterator[int32]:
    r = via_method(b)
    if r is not None:
        yield r.x


# async body
async def in_async(b: B) -> None:
    r = via_method(b)
    if r is not None:
        print("async", r.x)


def read_x(b: B) -> int32:
    r = via_method(b)
    if r is None:
        return -1
    return r.x


def main() -> None:
    b = B()
    r = via_method(b)
    if r is not None:
        r.x += 1
    print("method", read_x(b))
    f = via_free(b)
    if f is not None:
        f.x += 10
    print("free", read_x(b))
    xs = via_container(b)
    if xs is not None:
        xs.append(9)
    src = b._xs
    print("container", len(src) if src is not None else -1)
    s = b.ret_self()
    if s is not None:
        s.x += 100
    print("self", read_x(b))
    h = Host()
    hm = h.via_method(b)
    if hm is not None:
        hm.x += 1000
    print("host_method", read_x(b))
    hx = h.via_container(b)
    if hx is not None:
        hx.append(7)
    hsrc = b._xs
    print("host_container", len(hsrc) if hsrc is not None else -1)
    for v in in_generator(b):
        print("generator", v)
    asyncio.run(in_async(b))
    # the const alias can only be read, so the source is mutated by another
    # path: a copy instead of the alias would print the stale value
    ro = b.opt_ro()  # tpyc: ok
    osrc = b._opt
    if osrc is not None:
        osrc.x += 7
    print("readonly_opt", ro.x if ro is not None else -1)


main()
