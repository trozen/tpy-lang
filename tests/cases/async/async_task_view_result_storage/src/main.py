# Awaiting a `Task` whose payload is a view: the task handle stays outside
# the record slice, so its declaration takes the storage row.
import asyncio
from tpy import BytesView


async def sub() -> BytesView:
    return b"x"


async def main_coro() -> None:
    # The task's payload type keeps this declaration on the storage row.
    t = asyncio.create_task(sub())
    print(len(await t))


def main() -> None:
    asyncio.run(main_coro())


main()
