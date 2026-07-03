# Mutually recursive bound coroutines form the same by-value embedding
# cycle as direct recursion -- rejected via the topological emit order.
import asyncio


async def pong(n: int) -> int:  # tpyc: error(/recursive coroutine embedding/)
    if n <= 0:
        return 0
    c = ping(n - 1)
    return 1 + await c


async def ping(n: int) -> int:
    if n <= 0:
        return 0
    c = pong(n - 1)
    return 1 + await c


def main() -> None:
    print(asyncio.run(ping(4)))


main()
