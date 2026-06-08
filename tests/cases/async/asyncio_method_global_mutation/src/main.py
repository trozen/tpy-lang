# Regression: a `global` written inside an async METHOD, after an `await`
# suspension, must reach the module slot (not a resumable-frame copy) so the
# awaiter observes it. Exercises the method-path generator_locals fix and the
# post-suspension (resume-state) write that the free-function test omits.
import asyncio
from tpy import Int32

total: Int32 = 0


class Worker:
    async def add(self, n: Int32) -> None:
        global total
        await asyncio.sleep(0)
        total += n


async def main_coro() -> None:
    w = Worker()
    await w.add(5)
    print("total =", total)
    await w.add(3)
    print("total =", total)


def main() -> None:
    asyncio.run(main_coro())


main()
