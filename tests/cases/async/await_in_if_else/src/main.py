# `await` inside both branches of an if/else.
import asyncio

async def value(n: int) -> int:
    return n

async def pick(cond: bool) -> int:
    if cond:
        x = await value(10)
    else:
        x = await value(20)
    return x

def main() -> None:
    print(asyncio.run(pick(True)))
    print(asyncio.run(pick(False)))

main()
