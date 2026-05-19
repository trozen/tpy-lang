# Async method body containing an asyncio.sleep (real suspension via
# Task[None]). Validates that an erased-mode await coexists with the
# method's coro-struct field layout.
import asyncio


class Worker:
    name: str

    def __init__(self, n: str) -> None:
        self.name = n

    async def run(self) -> str:
        await asyncio.sleep(0.01)
        return self.name + " done"


async def main_coro() -> None:
    w = Worker("alice")
    msg = await w.run()
    print(msg)


asyncio.run(main_coro())
