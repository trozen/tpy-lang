# Smoke test: @readonly async method body setup runs (`compute_body_const_sets`
# threading `const_ref_params={'self'}` through `setup_body_scope`). No
# behavioral-delta guard -- const_ref_params consumers in async bodies are
# unreachable today (generator-body for-loop bypasses const-source detection;
# var-decl bypass short-circuits const-aware dispatch). The reachable case
# (reference-type self.field iteration) is blocked by a separate filed bug.
import asyncio
from tpy import Int32, readonly


class Counter:
    items: list[Int32]

    def __init__(self) -> None:
        self.items = []
        self.items.append(1)
        self.items.append(2)
        self.items.append(3)

    @readonly
    async def total(self) -> Int32:
        s: Int32 = 0
        await asyncio.sleep(0)
        for v in self.items:
            s += v
        return s


async def driver() -> None:
    c = Counter()
    print(await c.total())


asyncio.run(driver())
