# Recursion through create_task works: the Task is the heap indirection
# that breaks the by-value frame-embedding cycle (the escape the
# recursive-embedding diagnostic names).
import asyncio


async def fact(n: int) -> int:
    if n <= 1:
        return 1
    t = asyncio.create_task(fact(n - 1))
    r = await t
    return n * r


def main() -> None:
    print(asyncio.run(fact(5)))


main()
