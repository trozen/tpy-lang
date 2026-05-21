# Regression: async coro body iterates a list[Item] (reference-type
# elements) with no internal await in the loop. The iter var is emitted
# as a C++ local (`const auto& it = *__beg_0;`), but sema added it to
# `func.generator_locals`, which puts it in `generator_optional_fields`
# via `setup_resumable_frame_locals`. Body-emit then misapplied the
# `(*it)` peel meant for std::optional-storage frame fields, breaking
# the C++ build. Fix: the for-loop emit registers the iter var in
# `frame_field_shadows` for the loop body's duration; body-emit
# suppresses the peel when a name is in that set.
import asyncio
from tpy import Int32


class Item:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


class Container:
    items: list[Item]

    def __init__(self) -> None:
        self.items = []
        self.items.append(Item(1))
        self.items.append(Item(2))
        self.items.append(Item(3))

    async def total(self) -> Int32:
        s: Int32 = 0
        await asyncio.sleep(0)
        for it in self.items:
            s += it.n
        return s


async def driver() -> None:
    c = Container()
    print(await c.total())


asyncio.run(driver())
