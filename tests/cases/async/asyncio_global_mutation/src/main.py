# Regression: a `global` mutated inside an async coroutine must reach the
# module slot, not a frame-field copy, so the awaiter observes the write.
import asyncio

ran: bool = False
hits: int = 0


async def bg() -> None:
    global ran, hits
    ran = True
    hits += 1


async def main_coro() -> None:
    asyncio.create_task(bg())
    await asyncio.sleep(0.01)
    print("ran =", ran)
    print("hits =", hits)


def main() -> None:
    asyncio.run(main_coro())


main()
