# Event is the no-payload completion signal -- producer set(), consumer
# awaits and resumes. Also covers fast-path (await on already-set Event
# resolves immediately) and set() idempotency.
import asyncio
from asyncio import Event


async def producer(e: Event) -> None:
    await asyncio.sleep(0.001)
    e.set()
    print("producer set")


async def consumer(e: Event) -> None:
    await e
    print("consumer woke")


async def fast_path_consumer(e: Event) -> None:
    await e
    print("fast-path woke")


async def main_coro() -> None:
    e_fast = Event()
    print(e_fast.is_set())
    # Second set() must be a no-op, not a double-wake.
    e_fast.set()
    e_fast.set()
    print(e_fast.is_set())
    asyncio.create_task(fast_path_consumer(e_fast))

    e = Event()
    print(e.is_set())
    asyncio.create_task(consumer(e))
    asyncio.create_task(producer(e))
    await asyncio.sleep(0.005)
    print(e.is_set())
    e.clear()
    print(e.is_set())


def main() -> None:
    asyncio.run(main_coro())


main()
