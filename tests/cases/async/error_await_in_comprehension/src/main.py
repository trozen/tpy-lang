# `await` inside a comprehension is not yet supported (the per-iteration
# suspension needs a desugar-to-loop that the pre-sema linearizer does not
# do). It must reject cleanly rather than miscompile.
import asyncio


async def f(x: int) -> int:
    return x * 10


async def main() -> None:
    xs = [1, 2, 3]
    r = [await f(x) for x in xs]  # tpyc: error(/comprehension/)
    print(r)


asyncio.run(main())
