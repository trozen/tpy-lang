# Binding a coroutine in a sync context (plain unique_ptr local, no frame)
# and driving it with asyncio.run.
import asyncio


async def add_one(n: int) -> int:
    return n + 1


def main() -> None:
    c = add_one(41)
    print(asyncio.run(c))


main()
