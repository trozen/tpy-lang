# What a frame holder holds and how it is spelled: a module-qualified callee,
# a bound generator of another module beside a same-named one here, an
# annotated task local, a str / bytes value at a deduced `Iterable` slot, a
# generator expression capturing a local, and a `*args` pack of fresh
# elements under a conditional operand.
import asyncio
import sys
from asyncio import Task
from typing import Iterable, Iterator
from tpy import Own, char, int32, uint8
import plan_lib


def gen(s: plan_lib.Src) -> Iterator[int32]:
    yield -s.n


async def qualified() -> int32:
    # an awaited module-qualified call over a fresh argument
    a = await plan_lib.co(plan_lib.Src(5))  # tpyc: ok
    t = 0
    for v in plan_lib.gen(plan_lib.Src(7)):  # tpyc: ok
        await asyncio.sleep(0)
        t += v
    return a + t


def bound_other() -> Own[list[int32]]:
    # a bound generator of the other module, while this one defines `gen`
    # (in a frame body such a local is refused,
    # BUGS.md#frame-local-cross-module-generator-refused)
    g = plan_lib.gen(plan_lib.Src(2))  # tpyc: ok
    out: list[int32] = []
    for v in g:
        out.append(v)
    return out


async def drain_it(it: Iterable[int32]) -> int32:
    await asyncio.sleep(0)
    s = 0
    for x in it:
        s += x
    return s


async def task_local() -> int32:
    # an annotated task local keeps its comprehension argument
    t: Task[int32] = asyncio.create_task(drain_it([x for x in range(5)]))  # tpyc: ok
    junk = [[999] * 20 for _ in range(10)]
    return await t + len(junk) - 10


def count(xs: Iterable[char]) -> Iterator[int32]:
    for x in xs:
        yield 1


def countb(xs: Iterable[uint8]) -> Iterator[int32]:
    for x in xs:
        yield int32(x)


async def total(xs: Iterable[char]) -> int32:
    await asyncio.sleep(0)
    n = 0
    for x in xs:
        n += 1
    return n


def literal_gen(k: str) -> Iterator[int32]:
    # a str literal, an f-string, a concatenation and a bytes literal a
    # generator head keeps at a deduced `Iterable` slot
    for x in count('abc'):  # tpyc: ok
        yield x
    for x in count(f"{k}!"):  # tpyc: ok
        yield x
    for x in count(k + "zz"):  # tpyc: ok
        yield x
    for x in countb(b'ab'):  # tpyc: ok
        yield x
    for x in count(k * 3):  # tpyc: ok
        yield x


async def totalb(xs: Iterable[uint8]) -> int32:
    await asyncio.sleep(0)
    n = 0
    for x in xs:
        n += x
    return n


async def literal_co(k: str) -> int32:
    # ... and an awaited coroutine
    a = await total('abcd')  # tpyc: ok
    b = await total(f"{k}?")  # tpyc: ok
    c = await totalb(b'ab')  # tpyc: ok
    return a * 100 + b + c * 1000


def relay(xs: Iterable[int32]) -> Iterator[int32]:
    for x in xs:
        yield x


def genexpr_capture_gen(n: int32) -> Iterator[int32]:
    # a generator expression capturing a local, kept by a generator head
    for x in relay(i + n for i in range(3)):  # tpyc: ok
        yield x


async def genexpr_capture_co() -> int32:
    # ... and by an awaited coroutine
    k = "abc"
    src = ["v" * 40, "w" * 41]
    return await drain_it(len(t) + len(k) for t in src)  # tpyc: ok


def size_all(*args: list[int32]) -> int32:
    s = 0
    for a in args:
        s += len(a)
    return s


def mk(n: int32) -> Own[list[int32]]:
    print("holders mk", n)
    return [n] * n


def pick(c: bool) -> int32:
    # a `*args` pack of fresh elements, built only when its arm runs
    return 5 if c else size_all(mk(1), mk(2))  # tpyc: ok


def main() -> None:
    print("holders qualified", asyncio.run(qualified()))
    print("holders bound other", bound_other(),
          list(gen(plan_lib.Src(1))))
    print("holders task local", asyncio.run(task_local()))
    print("holders literal gen", list(literal_gen("q")))
    print("holders literal co", asyncio.run(literal_co("p")))
    print("holders genexpr gen", list(genexpr_capture_gen(10)))
    print("holders genexpr co", asyncio.run(genexpr_capture_co()))
    c = len(sys.argv) > 5
    taken = pick(c)
    skipped = pick(not c)
    print("holders pack", taken, skipped)
