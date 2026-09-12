# Regression: guards two fixes that interlock at the same emit site.
# (1) `compute_body_const_sets` populates `const_ref_params={'self'}`
#     for @readonly async methods so `iteration_yields_const` fires for
#     `self.field` -> loop var binds as `const auto& it` (const ref).
# (2) `frame_field_shadows` suppresses the `(*it)` peel that would
#     otherwise misfire because sema added `it` to `generator_locals`
#     while codegen emits it as a C++-scoped local.
# Both fixes are required for this body to compile and emit correct
# const code; the `const auto& it = *__beg_0;` line in the snapshot is
# the load-bearing assertion.
import asyncio
from tpy import int32, readonly


class Item:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Container:
    items: list[Item]

    def __init__(self) -> None:
        self.items = []
        self.items.append(Item(1))
        self.items.append(Item(2))
        self.items.append(Item(3))

    @readonly
    async def total(self) -> int32:
        s: int32 = 0
        await asyncio.sleep(0)
        for it in self.items:
            s += it.n
        return s


async def driver() -> None:
    c = Container()
    print(await c.total())


asyncio.run(driver())
