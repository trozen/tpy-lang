# A directly recursive bound coroutine embeds its own frame by value --
# infinite-size; rejected with the create_task escape named.
import asyncio


async def fact(n: int) -> int:  # tpyc: error(/recursive coroutine embedding involving 'fact'/)
    if n <= 1:
        return 1
    c = fact(n - 1)
    r = await c
    return n * r


def main() -> None:
    print(asyncio.run(fact(5)))


main()
