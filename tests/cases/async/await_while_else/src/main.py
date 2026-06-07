# `while await cond(): ... else: ...` with an awaited condition: the else
# clause runs on normal (condition-false) exit and is skipped on break.
import asyncio


async def below(i: int, limit: int) -> bool:
    return i < limit


async def normal_exit() -> None:
    i = 0
    while await below(i, 3):
        print("iter", i)
        i += 1
    else:
        print("else ran")


async def break_exit() -> None:
    i = 0
    while await below(i, 10):
        if i == 2:
            break
        print("b-iter", i)
        i += 1
    else:
        print("else should NOT run")


async def main() -> None:
    await normal_exit()
    await break_exit()


asyncio.run(main())
