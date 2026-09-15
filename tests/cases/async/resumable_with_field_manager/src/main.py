# A `with` whose manager is a FIELD read (`with h.g:`) inside a resumable
# body. The sync route already borrows such a manager; the resumable gate
# admitted only a bare NAME, so the same statement rejected in a generator or
# `async def`. Mutate-and-observe: __enter__/__exit__ move the manager's own
# counter, so a copy instead of a borrow would leave `h.g.depth` untouched.
import asyncio
from typing import Iterator

from tpy import int32


class G:
    depth: int32

    def __init__(self) -> None:
        self.depth = 0

    def __enter__(self) -> int32:
        self.depth += 1
        return self.depth

    def __exit__(self, et, ev, tb) -> None:
        self.depth -= 1


class H:
    g: G

    def __init__(self) -> None:
        self.g = G()


# sync: the route the resumable gate mirrors
def plain(h: H) -> int32:
    with h.g as v:  # tpyc: ok
        return v + h.g.depth


# generator: the field manager spans a yield
def gen(h: H) -> Iterator[int32]:
    with h.g as v:  # tpyc: ok
        yield v
    yield h.g.depth


# async: the same manager across a suspension
async def coro(h: H) -> int32:
    with h.g as v:  # tpyc: ok
        await asyncio.sleep(0)
        return v + h.g.depth


def main() -> None:
    h = H()
    print("plain", plain(h))
    print("plain after", h.g.depth)
    for x in gen(h):
        print("gen", x)
    print("gen after", h.g.depth)
    print("coro", asyncio.run(coro(h)))
    print("coro after", h.g.depth)


main()
