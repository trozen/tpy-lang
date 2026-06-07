# `await` inside a dict comprehension hits the same not-yet-supported reject
# as list comprehensions (the desugar's reject covers all comprehension kinds).
import asyncio


async def f(x: int) -> int:
    return x * 10


async def main() -> None:
    xs = [1, 2, 3]
    r = {x: await f(x) for x in xs}  # tpyc: error(/comprehension/)
    print(r)


asyncio.run(main())
