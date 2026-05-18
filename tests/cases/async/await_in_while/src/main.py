# `await` inside a `while` loop body.
import asyncio

async def value(n: int) -> int:
    return n

async def loop_sum(n: int) -> int:
    total = 0
    i = 0
    while i < n:
        total = total + await value(1)
        i = i + 1
    return total

def main() -> None:
    print(asyncio.run(loop_sum(5)))

main()
