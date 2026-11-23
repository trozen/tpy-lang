# The tuple-member shapes MIR does not analyze yet, each pinned with its reason.
import asyncio
from typing import Iterator

from tpy import int32

from fixtures import P


# generator: a frame holding a member loan across a yield
def gen_member() -> Iterator[int32]:  # tpyc: mir(uncovered /unsupported hoisted value/)
    t = (P(1), 2)
    a = t[0]
    yield a.v
    a.v = 3
    yield t[0].v


# async: the member bound in a coroutine
async def async_member() -> int32:  # tpyc: mir(uncovered /async body/)
    t = (P(1), 2)
    r = t[0]
    r.v = 5
    await asyncio.sleep(0)
    return t[0].v


# try/finally: the member bound inside a guarded block
def guarded() -> int32:  # tpyc: mir(uncovered /unsupported statement/)
    t = (P(1), 2)
    try:
        r = t[0]
        r.v = 6
    finally:
        t[0].v += 1
    return t[0].v


# a tuple mixing an inline member and a parameter's pointer member
def mixed(p: P) -> int32:  # tpyc: mir(uncovered /missing or invalid layout fact/)
    t = (P(2), p)
    a = t[0]
    b = t[1]
    a.v += b.v
    return t[0].v


def main() -> None:
    print("gen", list(gen_member()))
    print("async", asyncio.run(async_member()))
    print("guarded", guarded())
    p = P(3)
    print("mixed", mixed(p), p.v)
