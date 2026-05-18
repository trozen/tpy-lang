# `await` inside a sync `for x in <list>:` body. Verifies the
# universal iter/next path works for built-in NativeIterable
# containers, not just `range`.
import asyncio

async def value(n: int) -> int:
    return n + 100

async def total_of(xs: list[int]) -> int:
    total = 0
    for x in xs:
        total = total + await value(x)
    return total

def main() -> None:
    print(asyncio.run(total_of([1, 2, 3])))

main()
