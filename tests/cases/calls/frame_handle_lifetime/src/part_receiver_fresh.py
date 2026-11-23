# An awaited method whose receiver is reached through a fresh object -- a
# Deref wrapper, an element of a fresh container: the frame holds that object
# while the coroutine runs. An unproven Optional wrapper is checked before
# its `__deref__()`. A loop head holds the receiver of a `__getitem__` whose
# result points into it, builtin or user type alike.
import asyncio
from typing import Iterator

from tpy import Own, StrView, int32, nocopy, readonly
from tplib.box import Box
from tplib.rc import Rc


@nocopy
class Acc:
    base: int32

    def __init__(self, base: int32) -> None:
        self.base = base

    async def add(self, x: int32) -> int32:
        await asyncio.sleep(0.0)
        # Fresh allocations after the suspension reuse any storage the
        # receiver had freed; the write and the read go through the
        # receiver itself.
        junk = [Acc(999) for _ in range(4)]
        self.base += x + len(junk) - 4
        return self.base

    @readonly
    async def read(self) -> int32:
        await asyncio.sleep(0.0)
        junk = [Acc(999) for _ in range(4)]
        return self.base + len(junk) - 4


def mk_box(n: int32) -> Own[Box[Acc]]:
    return Box(Acc(n))


def mk_rc(n: int32) -> Own[Rc[Acc]]:
    return Rc.new(Acc(n))


def mk_list(n: int32) -> Own[list[Acc]]:
    return [Acc(n), Acc(n + 1)]


def mk_dict(n: int32) -> Own[dict[str, Acc]]:
    d: dict[str, Acc] = {}
    d["k"] = Acc(n)
    return d


async def wrappers(n: int32) -> None:
    # A constructed and a returned Box, and a returned Rc: the frame holds
    # the wrapper the `__deref__()` reads into.
    a = await Box(Acc(n)).read()  # tpyc: ok
    b = await mk_box(n + 10).add(6)  # tpyc: ok
    c = await mk_rc(n + 20).read()  # tpyc: ok
    print("fresh_wrapper", a, b, c)


async def elements(n: int32) -> None:
    # An element of a fresh list or dict: the frame holds the container.
    a = await mk_list(n)[0].read()  # tpyc: ok
    b = await mk_list(n)[1].add(5)  # tpyc: ok
    c = await mk_dict(n + 30)["k"].read()  # tpyc: ok
    print("fresh_element", a, b, c)


async def opt_box(b: Box[Acc] | None) -> int32:
    # An unproven Optional wrapper: checked before its `__deref__()`.
    return await b.add(3)  # tpyc: warning(/Potential None access/)


class ViewBag:
    items: list[str]

    def __init__(self, n: int32) -> None:
        self.items = ["bag" + str(n) * 30]

    def __getitem__(self, i: int32) -> StrView:
        return self.items[i]

    def first(self) -> StrView:
        return self.items[0]


class GBag:
    items: list[int32]

    def __init__(self, n: int32) -> None:
        self.items = [n]

    def __getitem__(self, i: int32) -> Acc:
        return SHARED


SHARED = Acc(70)


def sv_id(s: StrView) -> StrView:
    return s


def sv_chars(s: StrView) -> Iterator[str]:
    for c in s:
        junk = [Acc(999) for _ in range(4)]
        yield c * (len(junk) - 3)


def each_int(x: int32) -> Iterator[int32]:
    yield x
    yield x + 1


def each_acc(a: Acc) -> Iterator[int32]:
    yield a.base
    yield a.base * 2


def getitem_heads() -> None:
    # A view a `__getitem__` returns by value points into the receiver:
    # the loop head holds the receiver, at a borrowing call and a frame.
    out = ""
    for c in sv_id(ViewBag(5)[0]):  # tpyc: ok
        junk = [Acc(999) for _ in range(4)]
        out += c * (len(junk) - 3)
    print("getitem_view", out)
    out = ""
    for c in sv_chars(ViewBag(6)[0]):  # tpyc: ok
        out += c
    print("getitem_view_frame", out)
    # A by-value element of a builtin container: a number is copied in.
    for x in each_int(mk_ints(10)[1]):  # tpyc: ok
        print("getitem_int", x)
    # A reference `__getitem__` hands back from a global points into no
    # operand: the receiver is not held for it.
    for y in each_acc(GBag(3)[0]):  # tpyc: ok
        print("getitem_global", y)


async def co_view(s: StrView) -> str:
    await asyncio.sleep(0.0)
    junk = [Acc(999) for _ in range(4)]
    return str(s) + str(len(junk))


async def view_args(p: StrView) -> None:
    # A view of a parameter through a user function: durable, passed in place.
    print("view_param_await", await co_view(sv_id(p)))  # tpyc: ok


def view_heads(f: bool) -> None:
    s = "local" * 6
    # A view of a durable local through a user function, at a generator
    # argument, in a comprehension and awaited.
    print("view_local_gen", "".join(sv_chars(sv_id(s))))  # tpyc: ok
    print("view_local_comp", len([c for c in sv_chars(sv_id(s))]))  # tpyc: ok
    print("view_local_await", asyncio.run(co_view(sv_id(s))))  # tpyc: ok
    asyncio.run(view_args(s))
    # A select of views on fresh receivers: each arm seats its receiver.
    out = ""
    for c in sv_chars(ViewBag(7).first() if f else ViewBag(8).first()):  # tpyc: ok
        out += c
    print("view_select", out)
    # A handle bound to a local over views of fresh values: the frame holds
    # each one, and nothing warns.
    it = sv_chars(sv_id(mks(1)))  # tpyc: ok
    it2 = sv_chars(ViewBag(2).first())  # tpyc: ok
    junk = [Acc(999) for _ in range(4)]
    print("view_handle", "".join(it), "".join(it2), len(junk))


def mks(n: int32) -> str:
    return "mks" + str(n) * 20


def mk_ints(n: int32) -> Own[list[int32]]:
    return [n, n + 10]


def main() -> None:
    getitem_heads()
    view_heads(False)
    asyncio.run(wrappers(1))
    asyncio.run(elements(2))
    b = Box(Acc(40))
    print("fresh_opt_box", asyncio.run(opt_box(b)), b.base)
    try:
        asyncio.run(opt_box(None))
    except AttributeError:
        print("fresh_opt_box_none AttributeError")
