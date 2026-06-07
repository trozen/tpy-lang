# `await` inside a generator expression hits the same not-yet-supported
# reject as list/dict comprehensions.
import asyncio


async def f(x: int) -> int:
    return x * 10


async def main() -> None:
    xs = [1, 2, 3]
    total = sum(await f(x) for x in xs)  # tpyc: error(/comprehension|generator expression/)
    print(total)


asyncio.run(main())
