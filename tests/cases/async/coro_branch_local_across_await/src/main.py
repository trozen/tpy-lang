# A plain local first-assigned inside an if/elif/else/match branch and read
# across an await must survive the coroutine state-machine split (frame field).
import asyncio


async def coro_if(n: int) -> int:
    if n == 0:
        r = 100
    else:
        r = n + 1
    await asyncio.sleep(0)
    return r + 1


async def coro_elif(n: int) -> int:
    if n == 0:
        r = 1
    elif n == 1:
        r = 2
    else:
        r = n + 10
    await asyncio.sleep(0)
    return r + 1


async def coro_match(n: int) -> int:
    match n:
        case 0:
            r = 100
        case _:
            r = n + 1
    await asyncio.sleep(0)
    return r + 1


def main() -> None:
    print(asyncio.run(coro_if(5)))
    print(asyncio.run(coro_if(0)))
    print(asyncio.run(coro_elif(7)))
    print(asyncio.run(coro_match(5)))


main()
