# `await` inside the then-branch of an if (else has no await).
import asyncio

async def value(n: int) -> int:
    return n

async def maybe(cond: bool) -> int:
    if cond:
        x = await value(42)
    else:
        x = 0
    return x

def main() -> None:
    print(asyncio.run(maybe(True)))
    print(asyncio.run(maybe(False)))

main()
