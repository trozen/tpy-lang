# `await` inside nested if/else (await in each of four leaves).
import asyncio

async def value(n: int) -> int:
    return n

async def deep(a: bool, b: bool) -> int:
    if a:
        if b:
            x = await value(11)
        else:
            x = await value(10)
    else:
        if b:
            x = await value(1)
        else:
            x = await value(0)
    return x

def main() -> None:
    print(asyncio.run(deep(True, True)))
    print(asyncio.run(deep(True, False)))
    print(asyncio.run(deep(False, True)))
    print(asyncio.run(deep(False, False)))

main()
