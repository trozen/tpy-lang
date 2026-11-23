# A field read off a fresh object, at any depth: an lvalue of the object the
# statement seats (a field chain into a generator, a `str` field at a
# `StrView` head or await), the object a compound head keeps or encloses
# (a `with` target read after the statement), a field taken over by an
# `Own` frame parameter, and a list literal a frame head holds.
import asyncio
from typing import Iterable, Iterator
from tpy import Own, Ptr, StrView, int32


class Src:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Mid:
    s: Src
    name: str

    def __init__(self, n: int32) -> None:
        self.s = Src(n)
        self.name = "mid-name-long-enough-to-defeat-sso-" + str(n)


class Top:
    m: Mid

    def __init__(self, n: int32) -> None:
        self.m = Mid(n)


class Mgr:
    log: list[str]

    def __init__(self) -> None:
        self.log = []

    def __enter__(self) -> list[str]:
        self.log.append("enter")
        return self.log

    def __exit__(self, a: None, b: None, c: None) -> None:
        self.log.append("exit")


class Holder:
    m: Mgr

    def __init__(self) -> None:
        self.m = Mgr()

    def guard(self) -> Mgr:
        return self.m


class PtrMgr:
    p: Ptr[Src]

    def __init__(self, s: Src) -> None:
        s.n += 1
        self.p = s

    def __enter__(self) -> Src:
        return self.p

    def __exit__(self, a: None, b: None, c: None) -> None:
        pass


def gen_t[T](s: T) -> Iterator[T]:
    yield s


def gen_src(s: Src) -> Iterator[Src]:
    yield s


def gen_sv(s: StrView) -> Iterator[str]:
    yield str(s[0:8])


async def use_sv(s: StrView) -> str:
    await asyncio.sleep(0)
    return str(s[0:8])


# The callees below take their argument over and mutate it in place; the
# warning that nothing consumes the owned parameter further is expected.
def gen_own(s: Own[Src]) -> Iterator[int32]:  # tpyc: warning(/never consumed/)
    s.n += 1
    yield s.n


async def co_own(s: Own[Src]) -> int32:  # tpyc: warning(/never consumed/)
    await asyncio.sleep(0)
    s.n += 1
    return s.n


def g_it(it: Iterable[int32]) -> Iterator[int32]:
    for x in it:
        yield x


def consume(g: Iterator[Src]) -> int32:
    total = 0
    for s in g:
        s.n += 100
        total += s.n
    return total


def heads() -> Iterator[str]:
    # A `str` field of a fresh object at a `StrView` generator head.
    for x in gen_sv(Top(10).m.name):  # tpyc: ok
        yield "head " + x
    # An `Own` frame parameter takes a field of a fresh object over.
    for v in gen_own(Top(8).m.s):  # tpyc: ok
        yield "own " + str(v)
    # A list literal at a generator's `Iterable` parameter, across yields.
    for y in g_it([0, 1, 2]):  # tpyc: ok
        yield "lit " + str(y)


async def awaits() -> None:
    print("await sv", await use_sv(Top(5).m.name))  # tpyc: ok
    t = 0
    for i in range(3):
        t += await co_own(Top(i).m.s)  # tpyc: ok
    print("await own loop", t)


def main() -> None:
    # A field chain off a fresh object, monomorphic and generic.
    print("chain mono", consume(gen_src(Top(42).m.s)))  # tpyc: ok
    print("chain generic", consume(gen_t(Top(43).m.s)))  # tpyc: ok
    print("heads", list(heads()))
    asyncio.run(awaits())
    # A `with` target read after the statement borrows a field of a fresh
    # object: the object lives as long as the target.
    with Holder().guard() as q:  # tpyc: ok
        q.append("body")
    q.append("after")
    print("with field", q)
    # A constructor keeping the address of a field of a fresh object: used
    # inside the statement, or kept for a target read after it once the
    # object is a local (error_with_ctor_ptr_temp_read_after refuses the
    # fresh one, BUGS.md#ctor-ptr-store-not-a-loan).
    with PtrMgr(Top(6).m.s) as p:  # tpyc: ok
        print("with ptr inside", p.n)
    t = Top(7)
    with PtrMgr(t.m.s) as r:  # tpyc: ok
        pass
    r.n += 10
    print("with ptr after", r.n, t.m.s.n)
