# `await` inside a sync `for i in range(N):` body. v1.5 M3.1
# lowers the for-loop to the universal `::tpy::__iter__` /
# `__next__()` path so the iterator state lives in the coro
# frame across suspensions.
import asyncio

async def value(n: int) -> int:
    return n

async def sum_n(n: int) -> int:
    total = 0
    for i in range(n):
        total = total + await value(i)
    return total

def main() -> None:
    print(asyncio.run(sum_n(5)))

main()
