# A declared readonly Optional result binds and relays as a `const R*` alias of
# the source, never a copy: each section mutates the source, then reads the local.
import asyncio
from typing import Iterator, Optional, overload

from tpy import int32, readonly


class R:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class Guard:
    def __init__(self) -> None:
        pass

    def __enter__(self) -> int32:
        return 0

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


class H:
    _opt: Optional[R]
    _alt: Optional[R]
    _rec: R

    def __init__(self) -> None:
        self._opt = R(1)
        self._alt = R(2)
        self._rec = R(3)

    @readonly
    def get(self) -> readonly[Optional[R]]:
        return self._opt

    # the per-member spelling is the same declared type
    @readonly
    def get_member(self) -> readonly[R] | None:
        return self._alt

    @readonly
    def get_rec(self) -> readonly[R]:
        return self._rec

    def bump(self, n: int32) -> None:
        o = self._opt
        if o is not None:
            o.x += n
        a = self._alt
        if a is not None:
            a.x += n
        self._rec.x += n

    # a readonly field read bound inside `try` hoists as `const R*`
    @readonly
    def field_in_try(self) -> int32:
        r = -1
        try:
            t = self._opt  # tpyc: ok
            r = t.x if t is not None else -1
        finally:
            r += 1000
        return r


def fget(h: H) -> readonly[Optional[R]]:
    return h._opt


def relay_method(h: H) -> readonly[Optional[R]]:
    return h.get()  # tpyc: ok


def relay_free(h: H) -> readonly[Optional[R]]:
    return fget(h)  # tpyc: ok


@overload
def pick(h: H, k: int32) -> readonly[Optional[R]]: ...


@overload
def pick(h: H, k: str) -> readonly[Optional[R]]: ...


# the stubs' readonly result is the specialization's return type
def pick(h: H, k: int32 | str) -> Optional[R]:
    if isinstance(k, str):
        return h._alt
    return h._opt


@overload
def pick_ro(h: readonly[H], k: int32) -> readonly[Optional[R]]: ...


@overload
def pick_ro(h: readonly[H], k: str) -> readonly[Optional[R]]: ...


# a readonly receiver's field is the readonly result every stub declares
def pick_ro(h: readonly[H], k: int32 | str) -> readonly[Optional[R]]:  # tpyc: ok
    if isinstance(k, str):
        return h._alt
    return h._opt


def show(tag: str, v: readonly[Optional[R]]) -> None:
    print(tag, v.x if v is not None else -1)


def in_try(h: H) -> int32:
    r = -1
    try:
        # the call result bound inside `try` hoists as `const R*`
        t = h.get()  # tpyc: ok
        h.bump(10)
        r = t.x if t is not None else -1
    finally:
        r += 1000
    return r


def in_with(h: H) -> int32:
    with Guard() as gd:
        # the call result bound inside `with` hoists as `const R*`
        w = h.get()  # tpyc: ok
        h.bump(10)
        return (w.x if w is not None else -1) + gd


def param_in_with(h: H, p: readonly[Optional[R]]) -> int32:
    r = -1
    with Guard():
        # a readonly Optional parameter bound inside `with` hoists as `const R*`
        t = p  # tpyc: ok
        h.bump(10)
        r = t.x if t is not None else -1
    return r


def gen(h: H) -> Iterator[int32]:
    # the result bound in a generator frame survives the suspension
    m = h.get()  # tpyc: ok
    yield 0
    h.bump(10)
    yield m.x if m is not None else -1


async def co(h: H) -> int32:
    # the result bound in a coroutine frame survives the suspension
    m = h.get()  # tpyc: ok
    await asyncio.sleep(0)
    h.bump(10)
    return m.x if m is not None else -1


def main() -> None:
    h = H()
    # method decl
    m = h.get()  # tpyc: ok
    h.bump(10)
    print("method_decl", m.x if m is not None else -1)
    # free-function decl
    f = fget(h)  # tpyc: ok
    h.bump(10)
    print("free_decl", f.x if f is not None else -1)
    # reassignment: a local bound to the field, rebound to the readonly result
    r = h._alt
    r = h.get()  # tpyc: ok
    h.bump(10)
    print("reassign", r.x if r is not None else -1)
    # relay through a return, method and free function
    rm = relay_method(h)  # tpyc: ok
    rf = relay_free(h)  # tpyc: ok
    h.bump(10)
    show("relay_method", rm)
    show("relay_free", rf)
    # per-member spelling
    p = h.get_member()  # tpyc: ok
    h.bump(10)
    print("member", p.x if p is not None else -1)
    # @overload specialization declared readonly
    o = pick(h, "alt")  # tpyc: ok
    h.bump(10)
    print("overload", o.x if o is not None else -1)
    # @overload over a readonly receiver
    q = pick_ro(h, 0)  # tpyc: ok
    h.bump(10)
    print("overload_ro", q.x if q is not None else -1)
    # try / with bodies, a readonly field and a readonly parameter
    t = in_try(h)
    print("try", t)
    w = in_with(h)
    print("with", w)
    ft = h.field_in_try()
    print("field_try", ft)
    pw = param_in_with(h, h._opt)
    print("param_with", pw)
    # generator and coroutine frames
    for v in gen(h):
        print("gen", v)
    print("async", asyncio.run(co(h)))


main()

# module level: the global slot of a readonly result is `const R*`
h0 = H()
mod = h0.get()  # tpyc: ok
mod_rec = h0.get_rec()  # tpyc: ok
h0.bump(10)
print("module", mod.x if mod is not None else -1, mod_rec.x)
