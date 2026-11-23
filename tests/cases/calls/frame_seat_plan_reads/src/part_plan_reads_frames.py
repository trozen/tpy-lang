# Fresh arguments a frame keeps: a lambda's body runs apart from the
# coroutine that awaits it, a list literal and set / dict comprehensions at a
# generator's `Iterable` parameter in a frame head, a generator expression in
# a coroutine head, and a `@dynamic` protocol adapter a record keeps.
import asyncio
from typing import Callable, Iterable, Iterator, Protocol
from tpy import Own, Ptr, dynamic, int32, readonly


class Src:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Check:
    p: Ptr[readonly[Src]]

    def __init__(self, s: readonly[Src]) -> None:
        self.p = s

    def check(self, s: readonly[Src]) -> bool:
        self.p = s
        return s.n >= 0


def gen_refs(s: Src) -> Iterator[Src]:
    yield s


async def apply(f: Callable[[int32], int32]) -> int32:
    await asyncio.sleep(0)
    return f(10) + f(20)


async def lambda_apart() -> int32:
    # a comprehension in a lambda the coroutine awaits: its temporaries are
    # the lambda's own
    pred: Callable[[Src], bool] = lambda s: s.n >= 0
    a = await apply(
        lambda i: len([s.n for s in gen_refs(Src(i)) if Check(s).check(s)]))  # tpyc: ok
    # the lambda copies the callable it captures, as the warning says
    b = await apply(lambda i: len([s.n for s in gen_refs(Src(i)) if pred(s)]))  # tpyc: warning(/may keep its own copy of 'pred'/)
    return a + b


def g_it(it: Iterable[int32]) -> Iterator[int32]:
    for x in it:
        yield x


async def list_head() -> int32:
    # a list literal at a generator's `Iterable[int32]`, in a coroutine head
    t = 0
    for y in g_it([10, 20]):  # tpyc: ok
        await asyncio.sleep(0)
        t += y
    return t


def comps() -> int32:
    # set and dict comprehensions at that parameter, consumed in the call
    return sum(g_it({x for x in range(3)})) + sum(g_it({x: 1 for x in range(4)}))  # tpyc: ok


def gen_set_head() -> Iterator[int32]:
    # ... and a set comprehension in a generator head
    for y in g_it({x * 3 for x in range(3)}):  # tpyc: ok
        yield y


async def genexpr_co_head(xs: list[int32]) -> int32:
    # a generator expression in a coroutine head
    n = 0
    for y in g_it(x * 2 for x in xs):  # tpyc: ok
        await asyncio.sleep(0)
        n += y
    return n


@dynamic
class Has(Protocol):
    @readonly
    def first(self) -> int32: ...


class Bag:
    xs: list[int32]

    def __init__(self, n: int32) -> None:
        self.xs = [n, n + 100]

    @readonly
    def first(self) -> int32:
        return self.xs[0] + self.xs[1]


class View:
    p: Ptr[readonly[Has]]
    has: bool

    def __init__(self, p: Ptr[readonly[Has]], has: bool) -> None:
        self.p = p
        self.has = has

    def read(self) -> int32:
        if self.has:
            return self.p.first()
        return -1


def mk_view(h: Has | None, fallback: readonly[Has]) -> Own[View]:
    if h is None:
        return View(fallback, False)
    return View(h, True)


def walk_view(v: View) -> Iterator[int32]:
    yield v.read()
    yield v.read() + 1


def adapted() -> Iterator[int32]:
    # a fresh object adapted to an optional `@dynamic` protocol parameter,
    # in a generator head
    fb = Bag(0)
    for x in walk_view(mk_view(Bag(4), fb)):  # tpyc: ok
        junk = [[j] * 20 for j in range(50)]
        yield x + len(junk) - 50


def main() -> None:
    print("frames lambda", asyncio.run(lambda_apart()))
    print("frames list head", asyncio.run(list_head()))
    print("frames comps", comps(), sorted(gen_set_head()))
    print("frames genexpr co", asyncio.run(genexpr_co_head([1, 2, 3])))
    print("frames adapted", list(adapted()))
