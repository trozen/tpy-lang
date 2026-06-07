# `await` in a ternary: only the selected branch's coroutine is awaited,
# and an `await` in the condition (always evaluated once) composes with
# awaited or plain branches.
import asyncio


async def one(tag: str) -> int:
    print("eval", tag)
    return 1


async def two(tag: str) -> int:
    print("eval", tag)
    return 2


async def pick(tag: str, b: bool) -> bool:
    print("eval", tag)
    return b


async def main() -> None:
    cond = True
    x = await one("then-run") if cond else await two("else-skipped")
    print("x", x)
    cond2 = False
    y = await one("then-skipped") if cond2 else await two("else-run")
    print("y", y)
    # await in the condition AND both branches: condition runs once, only
    # the taken branch is awaited.
    z = await one("z-then-skip") if await pick("z-cond", False) else await two("z-else-run")
    print("z", z)
    # await only in the condition, plain branches.
    w = 10 if await pick("w-cond", True) else 20
    print("w", w)


asyncio.run(main())
