# More Callable member positions: an Optional field, a classmethod
# parameter, a method result, a nested Callable and an async method.
import asyncio
from typing import Callable
from fixtures import G, P, lend


class Maybe[T]:
    f: Callable[[], T] | None

    def __init__(self) -> None:
        self.f = None

    def set(self, f: Callable[[], T]) -> None:
        self.f = f

    def get(self) -> T:
        if self.f is None:
            raise ValueError("unset")
        return self.f()  # tpyc: ok


class Ops[T]:
    f: Callable[[], T]
    apply: Callable[[Callable[[], T]], T]

    def __init__(self, f: Callable[[], T],
                 apply: Callable[[Callable[[], T]], T]) -> None:
        self.f = f
        self.apply = apply

    @classmethod
    def run_with(cls, f: Callable[[], T]) -> T:
        return f()  # tpyc: ok

    def get_f(self) -> Callable[[], T]:
        return self.f

    def nested(self) -> T:
        return self.apply(self.f)  # tpyc: ok

    async def run_async(self) -> T:
        await asyncio.sleep(0)
        return self.f()  # tpyc: ok


def call0(f: Callable[[], P]) -> P:
    return f()


async def drive(o: Ops[P]) -> None:
    a = await o.run_async()
    a.n = 56
    print("async", G.n, a is G)


def main() -> None:
    # Optional field: called inside the class after narrowing, and outside
    mb = Maybe[P]()
    mb.set(lend)
    v = mb.get()
    v.n = 51
    print("optional-inside", G.n, v is G)
    if mb.f is not None:
        w = mb.f()  # tpyc: ok
        w.n = 52
    print("optional-outside", G.n)
    # classmethod: its Callable[[], T] parameter at the class's T
    c = Ops[P].run_with(lend)
    c.n = 53
    print("classmethod", G.n, c is G)
    o = Ops[P](lend, call0)
    # method result: the Callable[[], T] a method returns lends at P
    g = o.get_f()
    r = g()  # tpyc: ok
    r.n = 54
    print("method-result", G.n, r is G)
    # nested Callable member: the outer callable relays the inner's borrow
    n = o.nested()
    n.n = 55
    print("nested", G.n, n is G)
    # async method: the member's borrow crosses the suspension
    asyncio.run(drive(o))
