# A temporary tuple ELEMENT at a coroutine call in a loop would share one frame
# seat across every Task the loop creates, so it is refused.
import asyncio


class A:
    def __init__(self, v: int) -> None:
        self.v = v


async def co(p: tuple[A, A]) -> int:
    await asyncio.sleep(0)
    return p[0].v + p[1].v


async def ac() -> int:
    r = A(3)
    tasks: list[asyncio.Task[int]] = []
    for i in range(3):
        tasks.append(asyncio.create_task(co((A(i), r))))  # tpyc: error(/an element of argument 'p' of 'co'.*one shared slot/)
    total = 0
    for t in tasks:
        total += await t
    return total


def main() -> None:
    print("loop", asyncio.run(ac()))


main()
