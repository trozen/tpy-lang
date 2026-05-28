# Error: an `await` inside a `match` is rejected by the same suspension-shape
# check that rejects `yield` in a `match` for generators (the check is
# suspension-generic). `match` is the one compound the resumable CFG does
# not decompose.
import asyncio


async def sub(n: int) -> int:
    return n


async def caller(tag: int) -> int:
    match tag:  # tpyc: error(/inside a .match. statement is not yet supported/)
        case 0:
            return await sub(10)
        case _:
            return await sub(20)


def main() -> None:
    print(asyncio.run(caller(0)))


main()
