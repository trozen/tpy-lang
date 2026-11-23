# Names a statement binds that stay inside the pass keep nothing past it:
# a `with` target, a `match` capture, a nested def's loop, a generator
# expression over a bound view, a dict literal's awaited value.
import asyncio
from typing import Iterator, Self
from tpy import StrView, int32


class Src:
    text: str
    xs: list[int32]

    def __init__(self, n: int32) -> None:
        self.text = str(n) * 5
        self.xs = [n, n + 1]


async def view(s: Src) -> StrView:
    await asyncio.sleep(0)
    return s.text


class CM:
    s: StrView

    def __init__(self, s: StrView) -> None:
        self.s = s

    def __enter__(self) -> Self:
        return self

    def __exit__(self, a, b, c) -> None:
        pass


def total_len(it: Iterator[StrView]) -> int32:
    n = 0
    for s in it:
        n += len(s)
    return n


def sub_gen(s: Src) -> Iterator[int32]:
    yield s.xs[0]


def gen_outer() -> Iterator[int32]:
    def helper() -> int32:
        # a nested def's own loop: no frame of the enclosing generator
        total = 0
        for i in range(3):
            g = sub_gen(Src(i * 10))  # tpyc: ok
            for x in g:
                total += x
        return total

    yield helper()


async def in_pass() -> None:
    for i in range(3):
        # a `with` target and a `match` capture read only in the pass
        with CM(await view(Src(i))) as c:  # tpyc: ok
            print("targets with", c.s[:2])
        match await view(Src(i)):  # tpyc: ok
            case v:
                print("targets match", v[:2])
        # a generator expression over a bound view, drained in the pass
        w = await view(Src(i))
        print("targets genexpr", total_len(w[:2] for x in range(2)))  # tpyc: ok
        # a dict literal's awaited value, consumed in the pass
        d = {"k": len(await view(Src(i)))}  # tpyc: ok
        print("targets dict", d["k"])


def main() -> None:
    print("targets nested def", list(gen_outer()))
    asyncio.run(in_pass())
