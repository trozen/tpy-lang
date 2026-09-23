# An `Optional[container]` PARAMETER of a resumable frame -- a generator or
# an async def -- takes the same pointer-repr borrow binding its record twin
# takes (`T*`, narrowed reads through the pointer). Each section mutates
# through the binding and the caller reads its own object back, so a copy
# would show. (The tuple-element unpack inside a frame loop is the same
# binding but aliases the frame's tuple COPY for record and container alike:
# BUGS.md#frame-tuple-unpack-optional-element-aliases-copy.)
import asyncio
from typing import Iterator, Optional

from tpy import int32


class R:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


# generator, Optional[bytearray] parameter mutated after the narrow
def twice(b: Optional[bytearray]) -> Iterator[int32]:  # tpyc: ok
    if b is not None:
        b.append(7)
    yield 1
    yield 2


# generator, Optional[record] parameter -- the twin
def twice_r(r: Optional[R]) -> Iterator[int32]:  # tpyc: ok
    if r is not None:
        r.n += 7
    yield 1
    yield 2


# async, Optional[dict] parameter
async def fill(d: Optional[dict[str, int32]]) -> int32:  # tpyc: ok
    if d is None:
        return -1
    d["z"] = 9
    return len(d)


def main() -> None:
    buf = bytearray(b"a")
    print("gen_param", list(twice(buf)), len(buf))
    print("gen_param_none", list(twice(None)))
    r = R(1)
    print("gen_param_rec", list(twice_r(r)), r.n)
    d = {"a": 1}
    print("async_param", asyncio.run(fill(d)), asyncio.run(fill(None)), len(d))


main()
