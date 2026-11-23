# A fresh str / bytes value that owns its buffer at a frame callee a method,
# a generic or a coroutine method names, seated for as long as the keep plan
# says (a statement temporary where nothing suspends while it lives, else a
# frame holder); and a constructor argument a task keeps: a TPy constructor
# that only reads its `Iterable` keeps nothing of it. A call whose handle is
# stored or bound in the body (a task awaited there) is admitted; only a
# `return` / `yield` value hands its handle out of the frame. A select passes
# as the direct argument of an awaited call when each arm is such a value, a
# name or a literal.
import asyncio
from typing import Iterable, Iterator
from tpy import StrView, char, int32, uint8
import part_plan_reads_holders


def each_t[T](xs: Iterable[T]) -> Iterator[T]:
    for c in xs:
        yield c


def each(xs: Iterable[char]) -> Iterator[char]:
    for c in xs:
        yield c


def eachb(xs: Iterable[uint8]) -> Iterator[uint8]:
    for c in xs:
        yield c


def drain(it: Iterator[char]) -> int32:
    n = 0
    for c in it:
        n += 1
    return n


class G:
    k: int32

    def __init__(self) -> None:
        self.k = 0

    def each(self, xs: Iterable[char]) -> Iterator[char]:
        for c in xs:
            yield c

    async def count(self, xs: Iterable[char]) -> int32:
        await asyncio.sleep(0)
        n = 0
        for c in xs:
            n += 1
        return n

    async def octets(self, xs: Iterable[uint8]) -> int32:
        await asyncio.sleep(0)
        n = 0
        for x in xs:
            n += x
        return n


def gen_method(g: G, n: int32) -> Iterator[char]:
    # an f-string a method's generator keeps in a generator head
    for c in g.each(f"ab{n}"):  # tpyc: ok
        yield c


def gen_generic(n: int32) -> Iterator[char]:
    # ... a generic generator
    for c in each_t(f"ab{n}"):  # tpyc: ok
        yield c


async def co_method(g: G, s: str) -> int32:
    # a repetition and a bytes literal an awaited coroutine method keeps
    a = await g.count(s * 3)  # tpyc: ok
    b = await g.octets(b'abc')  # tpyc: ok
    return a * 1000 + b


class Summary:
    label: StrView
    total: int32

    def __init__(self, xs: Iterable[int32], label: StrView) -> None:
        self.label = label
        self.total = 0
        for x in xs:
            self.total += x


async def show(s: Summary) -> int32:
    await asyncio.sleep(0)
    return len(s.label) + s.total


async def eager_ctor() -> int32:
    # a constructor that sums its list: the task keeps only the label
    label = "hello"
    t = asyncio.create_task(show(Summary([1, 2, 3], label)))  # tpyc: ok
    return await t


class Keeper:
    src: list[int32]

    def __init__(self, xs: list[int32]) -> None:
        # the constructor copies its `list` argument into the field, so the
        # task and the generator below keep the Keeper, not the literal
        self.src = xs  # tpyc: warning(/copies list/)


async def keeper_total(k: Keeper) -> int32:
    await asyncio.sleep(0)
    return sum(k.src)


def keeper_each(k: Keeper) -> Iterator[int32]:
    for x in k.src:
        yield x


async def keeper_task() -> int32:
    # a constructor copying its `list` argument into the field, the Keeper
    # kept by a task
    t = asyncio.create_task(keeper_total(Keeper([1, 2, 3])))  # tpyc: ok
    return await t


def keeper_gen() -> Iterator[int32]:
    # ... and a generator head keeps the Keeper
    for x in keeper_each(Keeper([4, 5, 6])):  # tpyc: ok
        yield x


def flat_gen(g: G, n: int32) -> Iterator[str]:
    # a loop head whose body does not suspend: free, bytes, method, in an
    # `if`, and a module-qualified generator of another part
    out = ""
    for c in each(f"q{n}"):  # tpyc: ok
        out += str(c)
    t = 0
    for b in eachb(b'abc'):  # tpyc: ok
        t += 1
    if n > 0:
        for c in g.each(f"m{n}"):  # tpyc: ok
            out += str(c)
    for x in part_plan_reads_holders.count(f"ab{n}"):  # tpyc: ok
        t += x
    yield f"{out} {t}"


def stmt_gen(k: str, n: int32) -> Iterator[int32]:
    # consumed within its statement in a generator body
    a = drain(each(f"q{n}"))  # tpyc: ok
    b = len(list(each(k + "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz")))  # tpyc: ok
    yield a * 100 + b


async def stmt_co(n: int32) -> int32:
    # ... and in a coroutine, beside a loop head that does not suspend
    await asyncio.sleep(0)
    a = drain(each(f"q{n}"))  # tpyc: ok
    t = 0
    for c in each(f"ab{n}"):  # tpyc: ok
        t += 1
    return a * 100 + t


async def collect(xs: Iterable[char]) -> str:
    await asyncio.sleep(0)
    out = ""
    for c in xs:
        out += str(c)
    return out


async def stored_task(n: int32) -> str:
    # a task stored in a local dict and awaited in this body, and one bound
    # to a name and awaited after a suspension
    tasks: dict[str, asyncio.Task[str]] = {}
    tasks["one"] = asyncio.create_task(collect(f"abcdefghijklmnop_{n}_q"))  # tpyc: ok
    t = asyncio.create_task(collect(f"rstuvwxyz_{n}"))  # tpyc: ok
    await asyncio.sleep(0)
    one = tasks["one"]
    return await one + await t


async def returned_await(n: int32) -> str:
    # the awaited call's own argument in a return value: the statement runs
    # the call to completion
    return await collect(f"abcdefghijklmnop_{n}_r")  # tpyc: ok


async def awaited_select(g: G, n: int32) -> str:
    # a select of fresh and literal text as the direct argument of an
    # awaited call, bound, through a method, and in a return value: the
    # awaited frame holds the select's value
    a = await collect(f"abcdefghijklmnop_{n}_a" if n > 0 else "x")  # tpyc: ok
    b = await g.count(f"bcdefghijklmnop_{n}" if n > 0 else f"y{n}")  # tpyc: ok
    return a + " " + str(b) + " " + await collect("lit" if n > 0 else "z")  # tpyc: ok


async def awaited_select_local(n: int32) -> str:
    # ... an arm a local owns: the frame holds a copy when it is chosen
    t = f"local_{n}"
    a = await collect(t if n > 5 else f"cdefghijklmnop_{n}")  # tpyc: ok
    return a


async def awaited_select_ret(n: int32) -> str:
    # ... the whole return value awaiting it
    return await collect(f"cdefghijklmnop_{n}" or "w")  # tpyc: ok


async def awaited_select_param(s: str, n: int32) -> str:
    # ... an arm a `str` parameter the frame holds: copied into the owned
    # select beside a fresh arm, viewed beside a literal one
    a = await collect(s or f"defghijklmnop_{n}")  # tpyc: ok
    b = await collect(s and f"efghijklmnop_{n}")  # tpyc: ok
    c = await collect(s or "fallback")  # tpyc: ok
    d = await collect("lit" if n < 0 else s)  # tpyc: ok
    e = await collect((s if n > 1 else "q") if n > 0 else f"fghij_{n}")  # tpyc: ok
    return a + " " + b + " " + c + " " + d + " " + e


class Result:
    label: StrView
    size: int32

    def __init__(self, label: StrView, size: int32) -> None:
        self.label = label
        self.size = size


async def store_record(out: dict[str, Result], s: StrView, n: int32) -> None:
    # a record stored in a dict, holding the count a statement drains
    out["one"] = Result(s, drain(each(f"ab{n}")))  # tpyc: ok


class Row:
    def __init__(self, v: int32) -> None:
        self.v = v


def pick(rows: list[Row], key: Iterable[char]) -> Iterator[Row]:
    i = 0
    for c in key:
        yield rows[i % len(rows)]
        i += 1


def relay(rows: list[Row], n: int32) -> Iterator[Row]:
    # a loop head over a fresh f-string whose target the body yields: the
    # caller mutates the rows through it
    for r in pick(rows, f"ab{n}"):  # tpyc: ok
        yield r


def main() -> None:
    print("owning stored task", asyncio.run(stored_task(5)))
    print("owning returned await", asyncio.run(returned_await(6)))
    print("owning awaited select", asyncio.run(awaited_select(G(), 8)))
    print("owning awaited select local", asyncio.run(awaited_select_local(9)))
    print("owning awaited select ret", asyncio.run(awaited_select_ret(9)))
    print("owning awaited select param", asyncio.run(awaited_select_param("hi", 2)))
    print("owning awaited select param", asyncio.run(awaited_select_param("", -1)))
    print("owning awaited select param", asyncio.run(awaited_select_param("hi", 1)))
    out: dict[str, Result] = {}
    asyncio.run(store_record(out, "tag", 7))
    print("owning store record", out["one"].label, out["one"].size)
    rows = [Row(10), Row(20)]
    for r in relay(rows, 7):
        r.v += 1
    print("owning relay", rows[0].v, rows[1].v)
    g = G()
    print("owning flat gen", list(flat_gen(g, 3)))
    print("owning stmt gen", list(stmt_gen("ab", 300)))
    print("owning stmt co", asyncio.run(stmt_co(3000)))
    print("owning gen method", len(list(gen_method(g, 2))))
    print("owning gen generic", len(list(gen_generic(3))))
    print("owning co method", asyncio.run(co_method(g, "ab")))
    print("owning eager ctor", asyncio.run(eager_ctor()))
    print("owning keeper task", asyncio.run(keeper_task()))
    print("owning keeper gen", list(keeper_gen()))
