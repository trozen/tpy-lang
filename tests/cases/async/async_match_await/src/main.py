# H1: an `await` inside a `match` lowers on the resumable frame -- the same
# CFG `match` decomposition serves generators and `async def` (the dispatch
# is suspension-generic; only arm bodies suspend).
import asyncio


async def sub(n: int) -> int:
    return n


async def caller(tag: int) -> int:
    match tag:  # tpyc: ok
        case 0:
            return await sub(10)
        case _:
            return await sub(20)


def main() -> None:
    print(asyncio.run(caller(0)))
    print(asyncio.run(caller(7)))


main()
