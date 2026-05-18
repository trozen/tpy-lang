# `await` inside a `while` body with `break` from inside an if-then.
import asyncio

async def value(n: int) -> int:
    return n

async def go() -> int:
    total = 0
    i = 0
    while True:
        x = await value(i)
        if x >= 5:
            break
        total = total + x
        i = i + 1
    return total

def main() -> None:
    print(asyncio.run(go()))

main()
