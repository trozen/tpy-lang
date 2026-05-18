# `await` inside a `while` body with `continue` from inside an if-then.
import asyncio

async def value(n: int) -> int:
    return n

async def go(n: int) -> int:
    total = 0
    i = 0
    while i < n:
        i = i + 1
        x = await value(i)
        if x % 2 == 0:
            continue
        total = total + x
    return total

def main() -> None:
    print(asyncio.run(go(10)))

main()
