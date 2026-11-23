# A reference-type value an awaited call's frame captures as it is -- a
# field, an element, a select of names, a global, a borrowing accessor, a
# property, a borrowing call -- mutated through the frame and observed by
# the caller afterwards; a mixed fresh / borrowed `str` ternary bound in a
# coroutine and a generator body; and a ternary over an `or` of a view and a
# literal beside a fresh arm.
import asyncio
from typing import Iterable, Iterator, Protocol
from tpy import char, int32


class Bumpable(Protocol):
    def bump(self) -> int32: ...


class R:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def bump(self) -> int32:
        self.n += 1
        return self.n


class H:
    r: R

    def __init__(self) -> None:
        self.r = R(10)

    def get(self) -> R:
        return self.r

    @property
    def prop(self) -> R:
        return self.r


def pick(xs: list[R]) -> R:
    return xs[0]


async def go(b: Bumpable) -> int32:
    await asyncio.sleep(0)
    return b.bump()


G = R(50)


async def bumped(h: H, xs: list[R], a: R, b: R, c: bool) -> None:
    # each operand is the caller's object: the bump shows through it
    print("captured field", await go(h.r), h.r.n)  # tpyc: ok
    print("captured element", await go(xs[0]), xs[0].n)  # tpyc: ok
    print("captured select", await go(a if c else b), a.n, b.n)  # tpyc: ok
    print("captured global", await go(G), G.n)  # tpyc: ok
    print("captured get", await go(h.get()), h.r.n)  # tpyc: ok
    print("captured prop", await go(h.prop), h.r.n)  # tpyc: ok
    print("captured pick", await go(pick(xs)), xs[0].n)  # tpyc: ok
    print("captured fresh", await go(R(3)))  # tpyc: ok


async def bind_co(s: str, n: int32) -> str:
    # a fresh / borrowed `str` ternary bound across a suspension, and an
    # owned-local arm beside the parameter
    await asyncio.sleep(0)
    x = f"abc_{n}" if n > 0 else s  # tpyc: ok
    t = f"tt_{n}"
    y = t if n > 1 else s  # tpyc: ok
    await asyncio.sleep(0)
    return x + "|" + y


def bind_gen(s: str, n: int32) -> Iterator[str]:
    # ... and bound between a generator's yields
    yield "a"
    x = f"abc_{n}" if n > 0 else s  # tpyc: ok
    yield x


def nested_sync(s: str, n: int32) -> str:
    # an `or` of views as one arm of a ternary whose other arm is fresh
    return (s or "lit") if n > 0 else f"fresh_{n}"  # tpyc: ok


async def collect(xs: Iterable[char]) -> str:
    await asyncio.sleep(0)
    out = ""
    for c in xs:
        out += str(c)
    return out


async def nested_co(s: str, n: int32) -> str:
    # ... awaited, returned and bound across a suspension in a coroutine
    a = await collect((s or "lit") if n > 0 else f"fresh_abcdefghijklmnop_{n}")  # tpyc: ok
    x = (s or "lit") if n > 0 else f"fresh_abcdefghijklmnop_{n}"  # tpyc: ok
    await asyncio.sleep(0)
    return a + " " + x + " " + ((s or "lit") if n > 0 else f"fresh_{n}")  # tpyc: ok


def main() -> None:
    h = H()
    asyncio.run(bumped(h, [R(20)], R(1), R(2), True))
    asyncio.run(bumped(h, [R(30)], R(4), R(5), False))
    print("captured bind co", asyncio.run(bind_co("pp", 1)),
          asyncio.run(bind_co("pp", 0)), asyncio.run(bind_co("pp", 2)))
    print("captured bind gen", list(bind_gen("qq", 0)),
          list(bind_gen("qq", 2)))
    print("captured nested sync", nested_sync("hello", 1), nested_sync("", 1),
          nested_sync("x", 0))
    print("captured nested co", asyncio.run(nested_co("hi", 1)),
          asyncio.run(nested_co("", 1)), asyncio.run(nested_co("hi", 0)))
