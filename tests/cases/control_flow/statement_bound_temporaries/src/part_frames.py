# The statement-bound operands in resumable bodies: a generator and a
# coroutine. Their frames are the ones they have without the operand rules;
# a pack a plain callee reads (`gather`) is a local of the statement.
import asyncio
from typing import Iterator
from tpy import int32, StrView, readonly
from fixtures import P, mk, side, total


def sub(sv: StrView) -> Iterator[StrView]:
    # Yields twice: a view that dangled after the first resume shows.
    yield sv
    yield sv


def gen(name: str, fb: StrView) -> Iterator[StrView]:
    yield "start"
    # A select whose view arm's root the frame keeps (an empty `name`
    # takes the other arm).
    for x in sub((name * 20).strip() or fb):  # tpyc: ok
        yield x


def junk(s: str) -> str:
    return s + "-junk-long-heap-string-0123456789"


def gen_opt(xs: list[str], s: str | None) -> Iterator[str]:
    if s is not None:
        # A narrowed `str | None` or an element, handed straight to a frame
        # callee: the select is a view, the frame as it is without it.
        for x in sub(s or xs[0]):  # tpyc: ok
            j = junk(str(x))
            yield "gen-opt " + str(x) + j[0:1]


def items(xs: readonly[list[int32]]) -> Iterator[int32]:
    for x in xs:
        yield x


def nested(a: list[int32], b: list[int32],
           c: list[int32]) -> Iterator[int32]:
    # A nested lvalue select as a generator's argument: its held operand is
    # read inside the statement only, the lists it picks live anyway.
    for x in items((a or b) or c):  # tpyc: ok
        yield x


def pickp(n: int32, p: P) -> tuple[P, int32]:
    return p, n


def gen_unpack(p: P, flag: bool) -> Iterator[int32]:
    # A tuple unpack whose argument holds a guarded select operand: the
    # alias it binds outlives the yield and writes the caller's object.
    a, n = pickp((side("unpack", 3).n or 5) if flag else 0, p)  # tpyc: ok
    yield n
    a.n += 10
    yield a.n


async def asv(s: StrView) -> str:
    await asyncio.sleep(0)
    return str(s)


async def work(k: int32) -> int32:
    await asyncio.sleep(0)
    return k * 2


async def co_opt(xs: list[str], s: str | None) -> str:
    if s is not None:
        # The same select awaited.
        r = await asv(s or xs[0])  # tpyc: ok
        return r + junk(r)[0:1]
    return "none"


async def co(xs: list[str], n: int32) -> None:
    # Async: a select seated as the awaited call's argument (an empty
    # `xs[0]` takes the fresh right arm).
    r1 = await asv(xs[0] or f"fresh-rhs-long-string-{n}")  # tpyc: ok
    r2 = await asv(f"fresh-lhs-long-string-{n}" or xs[0])  # tpyc: ok
    print("async", r1, r2)
    # A pack of task pointers `gather` copies before the suspension.
    t1 = asyncio.create_task(work(1))
    t2 = asyncio.create_task(work(2))
    rs = await asyncio.gather(t1, t2)  # tpyc: ok
    print("gather", rs[0] + rs[1])
    flag = n > 0
    async_guard_r = flag and (side("async", 0).n or 5) > 0  # tpyc: ok
    print("async-guard", async_guard_r)
    await asyncio.sleep(0)
    found = mk("async", 1).ps[0].n in (1, 2)  # tpyc: ok
    print("async-in", found, total("a", xs[0] + "b"))


def main() -> None:
    for v in gen("gn", "fallback"):
        print("gen", v)
    for v in gen("", "fallback"):
        print("gen-empty", v)
    a: list[int32] = []
    b = [1, 2]
    c = [3]
    g = nested(a, b, c)
    # The generator reads the caller's list when it runs: after this.
    b[0] = 9
    for w in g:
        print("nested", w)
    up = P(1)
    for u in gen_unpack(up, True):
        print("gen-unpack", u)
    for u in gen_unpack(up, False):
        print("gen-unpack-skip", u)
    print("gen-unpack-alias", up.n)
    for v2 in gen_opt(["elem"], "param-long-heap-string-0123456789abcdef"):
        print(v2)
    for v2 in gen_opt(["elem"], ""):
        print(v2)
    print("async-opt", asyncio.run(co_opt(["elem"], "param-long-heap-0123456789")),
          asyncio.run(co_opt(["elem"], "")))
    asyncio.run(co(["hello"], 1))
    asyncio.run(co([""], 2))
