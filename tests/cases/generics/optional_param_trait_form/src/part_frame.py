# A generator frame captures its open-T `T | None` parameter in the
# instantiation's form: a class argument stays the caller's object, so a
# mutation made after the generator was created shows through it.
import asyncio
from typing import Iterator, Optional

from tpy import Send, int32, readonly

from fixtures import P, V


def gen[T](x: T | None) -> Iterator[bool]:  # tpyc: frame_send(no)
    yield x is None
    print("frame", x)  # tpyc: ok
    yield x is not None


def gen_send[T](x: Send[T] | None) -> Iterator[bool]:  # tpyc: frame_send(no)
    yield x is not None  # tpyc: ok


def gen_ro[T](x: Optional[readonly[T]]) -> Iterator[bool]:  # tpyc: frame_send(no)
    # The readonly payload's frame field is the const trait.
    yield x is not None  # tpyc: ok
    print("frame_ro", x)


def gen_ro_bar[T](x: readonly[T] | None) -> Iterator[bool]:  # tpyc: frame_send(no)
    yield x is not None  # tpyc: ok


def gen_send_ro[T](x: Send[readonly[T]] | None) -> Iterator[bool]:  # tpyc: frame_send(no)
    yield x is not None  # tpyc: ok
    print("frame_send_ro", x)


async def async_ro[T](x: Optional[readonly[T]]) -> bool:  # tpyc: frame_send(no)
    # The coroutine frame's readonly payload field is the const trait too.
    print("async_ro", x)  # tpyc: ok
    return x is not None


async def async_ro_bar[T](x: readonly[T] | None) -> bool:  # tpyc: frame_send(no)
    print("async_ro_bar", x)  # tpyc: ok
    return x is not None


async def async_send_ro[T](x: Send[readonly[T]] | None) -> bool:  # tpyc: frame_send(no)
    print("async_send_ro", x)  # tpyc: ok
    return x is not None


async def amain(p: P) -> None:
    # Both coroutines hold `p` from creation to the await: the mutation shows.
    co = async_ro(p)
    co_send = async_send_ro(p)
    co_bar = async_ro_bar(p)
    p.n = 13
    print("async_ro", await co, await co_send, await co_bar, await async_ro(1),
          await async_ro("s"), await async_send_ro(1), await async_ro_bar(1),
          await async_ro_bar("s"))


def main() -> None:
    # The lists are built before the line prints: the generator prints too
    # (BUGS.md#print-partial-output-on-raise).
    a = list(gen(1))
    b = list(gen(V(2)))
    c = list(gen("s"))
    print("frame", a, b, c)
    p = P(4)
    g = gen(p)
    p.n = 9
    d = list(g)
    print("frame", d)
    print("frame_send", list(gen_send(1)), list(gen_send("s")),
          list(gen_send(p)), list(gen_send(V(3))))
    # The const frame field still refers to `p`: the mutation shows.
    r = gen_ro(p)
    rs = gen_send_ro(p)
    p.n = 11
    e = list(r)
    f = list(rs)
    h = list(gen_ro(1))
    k = list(gen_send_ro("s"))
    # A ValueType record payload: the const trait's value form.
    m = list(gen_ro(V(5)))
    # A typed None (a bare `None` would instantiate `T = None`); the other two
    # readonly spellings refuse it (BUGS.md#readonly-arg-at-open-readonly-optional-refused).
    no: int32 | None = None
    q = list(gen_ro_bar(no))
    s = list(gen_ro_bar(p))
    print("frame_ro", e, f, h, k, m, q, s)
    asyncio.run(amain(p))
