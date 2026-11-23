# A coroutine or generator whose frame holds its own copy of a parameter (a
# `str` copied into an owned `std::string`) keeps nothing of the argument,
# so a clause target handed to it carries no borrow past the pass; a lambda
# capturing a `str` loop variable holds its own copy too, an unpacked `str`
# member is its own value, a mixed `Own` / borrow tuple follows its call
# and a nested def keeps only what it captures.
import asyncio
from asyncio import Task
from typing import Callable, Iterator
from tpy import Own, int32


class Src:
    n: int32
    text: str

    def __init__(self, n: int32) -> None:
        self.n = n
        self.text = "item-" + str(n) * 3


def gen_str(s: Src) -> Iterator[str]:
    yield s.text


def strs(s: str) -> Iterator[str]:
    yield s


def pairs(s: Src) -> Iterator[tuple[str, Src]]:
    yield ("key-" + str(s.n), s)


def pick(s: Src) -> Src:
    return s


def gen_ref(s: Src) -> Iterator[Src]:
    yield s


def gen_src_str(s: Src) -> Iterator[tuple[Src, str]]:
    yield (s, s.text)


def gen_twins(s: Src) -> Iterator[tuple[Src, Src]]:
    yield (s, s)


# The `Own` leaf is read in place: the section needs it owned beside the
# borrowed one, and the warning says it is never consumed.
def use_mixed(p: tuple[Own[Src], Src]) -> int32:  # tpyc: warning(/never consumed/)
    return p[0].n + p[1].n


async def co_mixed(p: tuple[Own[Src], Src]) -> int32:  # tpyc: warning(/never consumed/)
    await asyncio.sleep(0)
    return p[0].n + p[1].n


def mk(i: int32) -> str:
    return "abcdefghij"[i:i + 1] * 30


async def co_s(s: str) -> str:
    await asyncio.sleep(0)
    # The tail differs per pass, so a borrow of the wrong pass shows.
    return s[-4:]


async def drain(ts: list[Task[str]]) -> Own[list[str]]:
    out: list[str] = []
    for t in ts:
        out.append(await t)
    return out


async def tasks() -> Own[list[str]]:
    ts: list[Task[str]] = []
    for i in range(3):
        # the task's frame copies the `str` clause target and the `str`
        # member of an unpacked tuple, by comprehension and by loop
        ts += [asyncio.create_task(co_s(v)) for v in gen_str(Src(i))]  # tpyc: ok
        ts += [asyncio.create_task(co_s(k)) for k, s in pairs(Src(i))]  # tpyc: ok
        for k, s in pairs(Src(i + 10)):  # tpyc: ok
            ts.append(asyncio.create_task(co_s(k)))
        # a generator over a fresh `str` argument
        ts += [asyncio.create_task(co_s(w)) for w in strs(mk(i))]  # tpyc: ok
        # a `str` field read of a target or of a fresh argument's result
        for x in gen_ref(Src(i + 20)):  # tpyc: ok
            ts.append(asyncio.create_task(co_s(x.text)))
        ts.append(asyncio.create_task(co_s(pick(Src(i + 30)).text)))  # tpyc: ok
        # an unpacked `str` member bound by value beside a reference one
        for r, v in gen_src_str(Src(i + 40)):  # tpyc: ok
            ts.append(asyncio.create_task(co_s(v)))
        await asyncio.sleep(0)
    return await drain(ts)


async def captures() -> str:
    out = ""
    for i in range(3):
        # a lambda copies the `str` it captures, by loop and by
        # comprehension; each is called in its own pass, where the copy and
        # a Python closure agree
        fns: list[Callable[[], str]] = []
        for s in gen_str(Src(i)):  # tpyc: ok
            # Both warnings are true of the C++: each lambda copies its
            # `str`, and the loop binds it again on each pass.
            fns.append(lambda: s)  # tpyc: warning(/copies 's'/) warning(/'s' is bound again/)
        fns += [lambda: w for w in gen_str(Src(i + 5))]  # tpyc: warning(/copies 'w'/) warning(/'w' is bound again/)
        await asyncio.sleep(0)
        for f in fns:
            out += f() + "|"
    return out


async def consumed() -> int32:
    def helper(x: int32) -> int32:
        return x + 1

    total = 0
    for i in range(3):
        # a mixed tuple's borrowed leaf follows a call the statement
        # consumes; a nested def's parameter is not a capture
        total += use_mixed((Src(i), Src(i + 1)))  # tpyc: ok
        total += await co_mixed((Src(i * 10), Src(i * 10 + 1)))  # tpyc: ok
        for p in gen_twins(Src(i * 100)):  # tpyc: ok
            # The `Own` leaf of the loop's tuple is copied, as warned.
            total += use_mixed(p)  # tpyc: warning(/copies tuple/)
            total += await co_mixed(p)  # tpyc: warning(/copies tuple/)
        for x in gen_ref(Src(i * 1000)):  # tpyc: ok
            total += helper(x.n)
        await asyncio.sleep(0)
    return total


def main() -> None:
    got = asyncio.run(tasks())
    print("owned params tasks", got)
    out = asyncio.run(captures())
    print("owned params captures", out)
    n = asyncio.run(consumed())
    print("owned params consumed", n)
