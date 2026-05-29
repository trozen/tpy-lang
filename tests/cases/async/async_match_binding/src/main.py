# H1: `await` inside a `match` arm, plus a capture binding (`v`) read after
# the await in the same arm -- the binding is a frame field on the coroutine.
import asyncio


async def sub(n: int) -> int:
    return n * 10


async def caller(tag: int) -> int:
    match tag:
        case 0:
            return await sub(1)
        case v:
            r = await sub(v)
            return r + v


def main() -> None:
    print(asyncio.run(caller(0)))
    print(asyncio.run(caller(5)))


main()
