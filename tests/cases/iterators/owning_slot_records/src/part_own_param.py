# An `Own` parameter given up to an iteration is consumed (no "never
# consumed"): `copy(p)` as at an `Own` slot, in a `for` and a comprehension
# alike, and a generator yielding each element owned. Copies are intended.
import asyncio
from typing import Iterator
from tpy import Own, int32, copy


class R:
    def __init__(self, v: int32) -> None:
        self.v = v


def sink(xs: Own[list[R]]) -> Own[list[R]]:
    return xs


def scalar_copy(p: Own[list[R]]) -> int32:  # tpyc: ok
    # the scalar twin: `copy(p)` at an `Own` slot consumes `p`
    keep = sink(copy(p))
    keep[0].v = 9
    return keep[0].v * 10 + p[0].v


def for_copy(p: Own[list[R]]) -> int32:  # tpyc: ok
    keep: list[R] = []
    # the subject: a `for` over `copy(p)` consumes `p`
    for x in copy(p):
        keep.append(x)
    keep[0].v = 9
    return keep[0].v * 10 + p[0].v


def for_copy_body_credit(p: Own[list[R]]) -> int32:  # tpyc: ok
    keep: list[R] = []
    # the subject: the head's `copy(p)` consumes `p` though the body
    # consumes it too
    for x in copy(p):
        keep.append(x)
        tmp = sink(copy(p))
        tmp[0].v = 8
    keep[0].v = 9
    return keep[0].v * 10 + p[0].v


async def async_owned(xs: Own[list[R]]) -> Own[list[R]]:  # tpyc: ok
    keep: list[R] = []
    # the subject: an async body moving each element out consumes `xs`
    for r in xs:
        keep.append(r)
    return keep


def comp_copy(p: Own[list[R]]) -> int32:  # tpyc: ok
    # the subject: a comprehension over `copy(p)` consumes `p` too
    keep = [x for x in copy(p)]
    keep[0].v = 9
    return keep[0].v * 10 + p[0].v


def gen_owned(xs: Own[list[R]]) -> Iterator[Own[R]]:  # tpyc: ok
    # the subject: each element yielded owned consumes `xs`
    for r in xs:
        yield r


def gen_read(xs: Own[list[R]]) -> Iterator[int32]:  # tpyc: warning(/'xs' is never consumed/)
    # the read-only neighbour: nothing is handed over, so `xs` is not
    for r in xs:
        yield r.v


def main() -> None:
    print("own param copy", scalar_copy([R(1)]), for_copy([R(1)]),
          comp_copy([R(1)]))
    owned = [r for r in gen_owned([R(1), R(2)])]
    owned[0].v = 5
    print("own param gen", [r.v for r in owned], list(gen_read([R(3)])))
    print("own param body credit", for_copy_body_credit([R(1)]))
    got = asyncio.run(async_owned([R(1), R(2)]))
    got[0].v = 7
    print("own param async", [r.v for r in got])
