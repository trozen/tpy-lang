# A narrowed Optional FIELD read after an await is stale: other tasks
# may mutate the field while this coroutine is suspended, so sema kills
# field-path narrow facts at every suspension point.
import asyncio
from tpy import int32


class Holder:
    f: int32 | None

    def __init__(self) -> None:
        self.f = 3

    async def after_await(self) -> int32:
        if self.f is not None:
            await asyncio.sleep(0)
            return self.f  # tpyc: error(/Type mismatch in return value/)
        return -1


async def drive() -> None:
    h = Holder()
    print(await h.after_await())


asyncio.run(drive())
