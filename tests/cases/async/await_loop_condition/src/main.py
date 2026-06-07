# `while await cond():` must re-evaluate the awaited condition every
# iteration, and `continue` must re-enter the condition. Exercises the
# awaited loop condition plus continue in the body.
import asyncio


async def below(i: int, limit: int) -> bool:
    return i < limit


async def main() -> None:
    i = 0
    while await below(i, 4):
        i += 1
        if i == 2:
            continue
        print("body", i)
    print("done", i)


asyncio.run(main())
